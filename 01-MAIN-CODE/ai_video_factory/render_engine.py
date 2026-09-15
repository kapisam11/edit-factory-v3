"""Safe FFmpeg execution, media validation, concat, subtitles, and encoding."""
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from .hardware import choose_encoder, ffmpeg_preset_for

logger = logging.getLogger(__name__)


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _ffmpeg_timeout() -> int:
    timeout = int(os.environ.get("AIVF_FFMPEG_TIMEOUT_SECONDS", "3600"))
    if timeout <= 0:
        raise ValueError("FFmpeg timeout must be positive")
    return timeout


def _ffprobe_timeout() -> int:
    timeout = int(os.environ.get("AIVF_FFPROBE_TIMEOUT_SECONDS", "30"))
    if timeout <= 0:
        raise ValueError("FFprobe timeout must be positive")
    return timeout


def run_ffprobe(cmd: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    if not cmd or Path(cmd[0]).name != "ffprobe":
        raise ValueError("run_ffprobe expects an ffprobe argv list")
    timeout = timeout if timeout is not None else _ffprobe_timeout()
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFprobe timed out after {timeout}s") from exc


def validate_media_output(path: str, require_video: bool = True, require_audio: bool = False) -> dict:
    target = Path(path)
    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError(f"Media output missing or empty: {target}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required to validate media outputs")
    result = run_ffprobe([
        ffprobe, "-v", "error", "-show_entries", "format=duration,size",
        "-show_streams", "-of", "json", str(target),
    ])
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {target}: {result.stderr[-1000:]}")
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
    if not cmd or Path(cmd[0]).name != "ffmpeg":
        raise ValueError("run_ffmpeg expects an ffmpeg argv list")
    timeout = timeout if timeout is not None else _ffmpeg_timeout()
    try:
        return subprocess.run(cmd, check=True, timeout=timeout, capture_output=capture_output, text=capture_output)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg timed out after {timeout}s") from exc


def render_segment(src_clip: str, ss: float, duration: float, vf: str, dst: str) -> None:
    """Render one requested timeline beat without double-applying source offsets."""
    if duration <= 0:
        raise ValueError("render duration must be positive")
    encoder = choose_encoder()
    candidates = [encoder, "libx264"] if encoder in ("h264_nvenc", "hevc_nvenc") else ["libx264"]
    last_error = None
    # generate_clip_paths() already trims each clip to its source timeline range.
    # Seeking by the original timeline offset again would therefore seek twice.
    seek_start = 0.0 if Path(src_clip).parent.name == "_clips" else max(0.0, ss)
    for selected in candidates:
        extra = (
            ["-preset", "p5", "-rc", "vbr_hq", "-b:v", "6000k"]
            if selected in ("h264_nvenc", "hevc_nvenc")
            else ["-preset", "fast", "-crf", "23"]
        )
        vf_full = f"{vf},tpad=stop_mode=clone:stop_duration={float(duration):.3f}"
        cmd = [
            "ffmpeg", "-y", "-ss", str(seek_start), "-i", src_clip, "-t", str(duration),
            "-vf", vf_full, "-af", "apad", "-c:v", selected, *extra,
            "-c:a", "aac", "-b:a", "128k", "-shortest", dst,
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
    with open(concat_list_path, "w", encoding="utf-8", newline="\n") as f:
        for p in seq_files:
            safe_path = str(Path(p)).replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")


def concat_segments(concat_list_path: str, output_path: str, encoder: str = "libx264") -> None:
    preset = ffmpeg_preset_for(encoder)
    codec = preset.get("codec", "libx264")
    opts = ["-preset", preset.get("preset", "slow")]
    if "nvenc" in codec:
        opts += ["-rc", preset.get("rc", "vbr_hq"), "-b:v", preset.get("bitrate", "6000k")]
    else:
        opts += ["-crf", preset.get("crf", "20")]
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path,
        "-c:v", codec, *opts, "-c:a", "aac", "-movflags", "+faststart", output_path,
    ]
    try:
        run_ffmpeg(cmd)
        validate_media_output(output_path)
    except Exception:
        if "nvenc" not in codec:
            raise
        Path(output_path).unlink(missing_ok=True)
        fallback = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-c:a", "aac",
            "-movflags", "+faststart", output_path,
        ]
        run_ffmpeg(fallback)
        validate_media_output(output_path)


def _escape_filter_path(path: str) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def burn_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    filter_path = _escape_filter_path(srt_path)
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-vf", f"subtitles=filename='{filter_path}'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "copy",
        "-movflags", "+faststart", output_path,
    ]
    run_ffmpeg(cmd)
    validate_media_output(output_path)


def mix_voiceover(video_path: str, vo_path: str, output_path: str) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-i", vo_path, "-c:v", "copy",
        "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", "-shortest", output_path,
    ]
    run_ffmpeg(cmd)
    validate_media_output(output_path)