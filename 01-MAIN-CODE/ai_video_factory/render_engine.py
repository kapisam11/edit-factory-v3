"""Safe FFmpeg execution, media validation, concat, subtitles, and encoding."""
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .hardware import choose_encoder, ffmpeg_preset_for

logger = logging.getLogger(__name__)


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _bounded_timeout(env_name: str, default: int, maximum: int = 7200) -> int:
    try:
        timeout = int(os.environ.get(env_name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer") from exc
    if not 1 <= timeout <= maximum:
        raise ValueError(f"{env_name} must be between 1 and {maximum} seconds")
    return timeout


def _ffmpeg_binary() -> str:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("ffmpeg executable is required but was not found on PATH")
    return binary


def _ffprobe_binary() -> str:
    binary = shutil.which("ffprobe")
    if not binary:
        raise RuntimeError("ffprobe executable is required but was not found on PATH")
    return binary


def _validate_media_path(value: str) -> str:
    if not value or "\x00" in str(value):
        raise ValueError("media path is invalid")
    return str(value)


def _validate_media_input(value: str, label: str = "media") -> str:
    path = Path(_validate_media_path(value))
    if not path.is_file():
        raise FileNotFoundError(f"{label} input not found: {path}")
    return str(path)


def _validate_tool_argv(cmd: List[str], expected: str) -> List[str]:
    if not cmd:
        raise ValueError(f"command must start with {expected}")
    requested = Path(str(cmd[0])).name.lower()
    allowed = {expected.lower(), f"{expected}.exe"}
    if requested not in allowed:
        raise ValueError(f"command must start with {expected}")
    return list(cmd)


def run_ffprobe(cmd: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    cmd = _validate_tool_argv(cmd, "ffprobe")
    cmd[0] = _ffprobe_binary()
    timeout = timeout if timeout is not None else _bounded_timeout("AIVF_FFPROBE_TIMEOUT_SECONDS", 30)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFprobe timed out after {timeout}s") from exc


def validate_media_output(path: str, require_video: bool = True, require_audio: bool = False) -> dict:
    target = Path(_validate_media_path(path))
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"Media output missing or empty: {target}")
    result = run_ffprobe(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,format_name",
            "-show_streams",
            "-of",
            "json",
            str(target),
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {target}: {(result.stderr or '')[-1000:]}")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {target}") from exc
    streams = data.get("streams") or []
    if require_video and not any(s.get("codec_type") == "video" for s in streams):
        raise RuntimeError(f"Media output has no video stream: {target}")
    if require_audio and not any(s.get("codec_type") == "audio" for s in streams):
        raise RuntimeError(f"Media output has no audio stream: {target}")
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if duration <= 0:
        raise RuntimeError(f"Media output has no positive duration: {target}")
    return data


def run_ffmpeg(cmd: List[str], timeout: Optional[int] = None, capture_output: bool = False) -> subprocess.CompletedProcess:
    cmd = _validate_tool_argv(cmd, "ffmpeg")
    cmd[0] = _ffmpeg_binary()
    timeout = timeout if timeout is not None else _bounded_timeout("AIVF_FFMPEG_TIMEOUT_SECONDS", 3600)
    try:
        if capture_output:
            return subprocess.run(
                cmd,
                check=True,
                timeout=timeout,
                capture_output=True,
                text=True,
            )

        result = subprocess.run(
            cmd,
            check=False,
            timeout=timeout,
            stdout=None,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            if len(detail) > 2000:
                detail = detail[-2000:]
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(f"FFmpeg failed with exit code {result.returncode}{suffix}")
        if result.stderr:
            sys.stderr.write(result.stderr)
            sys.stderr.flush()
        return result
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"FFmpeg failed with exit code {exc.returncode}{suffix}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg timed out after {timeout}s") from exc


def render_segment(src_clip: str, ss: float, duration: float, vf: str, dst: str) -> None:
    if duration <= 0:
        raise ValueError("render duration must be positive")
    src_clip = _validate_media_input(src_clip, "source clip")
    encoder = choose_encoder()
    candidates = [encoder, "libx264"] if encoder in ("h264_nvenc", "hevc_nvenc") else ["libx264"]
    last_error = None
    seek_start = 0.0 if Path(src_clip).parent.name == "_clips" else max(0.0, ss)
    for selected in candidates:
        extra = (
            ["-preset", "p5", "-rc", "vbr_hq", "-b:v", "6000k"]
            if selected in ("h264_nvenc", "hevc_nvenc")
            else ["-preset", "fast", "-crf", "23"]
        )
        vf_full = f"{vf},tpad=stop_mode=clone:stop_duration={float(duration):.3f}"
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(seek_start),
            "-i",
            src_clip,
            "-t",
            str(duration),
            "-vf",
            vf_full,
            "-af",
            "apad",
            "-c:v",
            selected,
            *extra,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            _validate_media_path(dst),
        ]
        try:
            run_ffmpeg(cmd)
            validate_media_output(dst)
            return
        except Exception as exc:
            last_error = exc
            Path(dst).unlink(missing_ok=True)
            if selected != "libx264":
                logger.warning("Encoder %s failed; retrying with libx264: %s", selected, exc)
    raise RuntimeError(f"Render failed: {last_error}") from last_error


def write_concat_list(seq_files: List[str], concat_list_path: str) -> None:
    if not seq_files:
        raise ValueError("concat list cannot be empty")
    _ensure_dir(str(Path(concat_list_path).parent))
    with open(concat_list_path, "w", encoding="utf-8", newline="\n") as f:
        for p in seq_files:
            value = _validate_media_input(p, "concat input")
            if "\r" in value or "\n" in value:
                raise ValueError("media paths cannot contain newlines")
            safe_path = value.replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")


def concat_segments(concat_list_path: str, output_path: str, encoder: str = "libx264") -> None:
    if not Path(concat_list_path).is_file():
        raise FileNotFoundError(concat_list_path)
    preset = ffmpeg_preset_for(encoder)
    codec = preset.get("codec", "libx264")
    opts = ["-preset", preset.get("preset", "slow")]
    if "nvenc" in codec:
        opts += ["-rc", preset.get("rc", "vbr_hq"), "-b:v", preset.get("bitrate", "6000k")]
    else:
        opts += ["-crf", preset.get("crf", "20")]
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list_path,
        "-c:v",
        codec,
        *opts,
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        _validate_media_path(output_path),
    ]
    try:
        run_ffmpeg(cmd)
        validate_media_output(output_path)
    except Exception:
        if "nvenc" not in codec:
            raise
        Path(output_path).unlink(missing_ok=True)
        fallback = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list_path,
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            _validate_media_path(output_path),
        ]
        run_ffmpeg(fallback)
        validate_media_output(output_path)


def _escape_filter_path(path: str) -> str:
    return _validate_media_path(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def burn_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    video_path = _validate_media_input(video_path, "subtitle video")
    srt_path = _validate_media_input(srt_path, "subtitle file")
    filter_path = _escape_filter_path(srt_path)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        f"subtitles=filename='{filter_path}'",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "23",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        _validate_media_path(output_path),
    ]
    run_ffmpeg(cmd)
    validate_media_output(output_path)


def mix_voiceover(video_path: str, vo_path: str, output_path: str) -> None:
    video_path = _validate_media_input(video_path, "voiceover video")
    vo_path = _validate_media_input(vo_path, "voiceover audio")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        vo_path,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-shortest",
        _validate_media_path(output_path),
    ]
    run_ffmpeg(cmd)
    validate_media_output(output_path)
