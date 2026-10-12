"""Safe FFmpeg execution, media validation, concat, subtitles, and encoding."""
import json
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
import threading
from collections import deque
from pathlib import Path
from typing import List, Optional

from .ffmpeg_budget import slot
from .hardware import choose_encoder, ffmpeg_preset_for
from .production_guardrails import terminate_process_tree
from .runtime_config import runtime_config

logger = logging.getLogger(__name__)



class FFmpegExecutionError(RuntimeError):
    """Structured FFmpeg failure with a durable diagnostic reference."""

    def __init__(
        self,
        message: str,
        *,
        command_id: str,
        command: List[str],
        exit_code: int | None,
        duration_seconds: float,
        stderr_tail: str,
        stderr_path: str | None,
    ) -> None:
        super().__init__(message)
        self.command_id = command_id
        self.command = tuple(command)
        self.exit_code = exit_code
        self.duration_seconds = round(duration_seconds, 3)
        self.stderr_tail = stderr_tail
        self.stderr_path = stderr_path

    def to_dict(self) -> dict:
        return {
            "type": type(self).__name__,
            "command_id": self.command_id,
            "command": list(self.command),
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "stderr_tail": self.stderr_tail,
            "stderr_path": self.stderr_path,
        }


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _bounded_timeout(env_name: str, default: int, maximum: int = 7200) -> int:
    config = runtime_config()
    known = {
        "AIVF_FFMPEG_TIMEOUT_SECONDS": config.ffmpeg_timeout_seconds,
        "AIVF_FFPROBE_TIMEOUT_SECONDS": config.ffprobe_timeout_seconds,
    }
    if env_name in known:
        return known[env_name]
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


def _run_ffmpeg_streaming(
    cmd: List[str],
    timeout: int,
    diagnostics_dir: str | Path | None = None,
    *,
    capture_stdout: bool = False,
    mirror_stderr: bool = True,
) -> subprocess.CompletedProcess:
    """Run FFmpeg with bounded capture, durable stderr logs, and tree-safe timeouts."""
    started = time.perf_counter()
    command_id = hashlib.sha256(
        (repr(cmd) + str(time.time_ns())).encode("utf-8")
    ).hexdigest()[:16]
    stdout_limit = 0
    if capture_stdout:
        try:
            stdout_limit = int(os.environ.get("AIVF_FFMPEG_CAPTURE_LIMIT_BYTES", str(4 * 1024**2)))
        except ValueError as exc:
            raise ValueError("AIVF_FFMPEG_CAPTURE_LIMIT_BYTES must be an integer") from exc
        if not 1024 <= stdout_limit <= 64 * 1024**2:
            raise ValueError("AIVF_FFMPEG_CAPTURE_LIMIT_BYTES must be between 1024 and 67108864")

    if diagnostics_dir is not None:
        diagnostic_root = Path(diagnostics_dir)
    else:
        candidate = Path(str(cmd[-1])) if cmd else Path(".")
        diagnostic_root = (
            candidate.parent
            if candidate.name and not str(candidate).startswith("pipe:")
            else Path(".")
        )

    diagnostic_path = diagnostic_root / "diagnostics" / f"ffmpeg-{command_id}.stderr.log"
    try:
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostic_handle = diagnostic_path.open(
            "w", encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        raise FFmpegExecutionError(
            f"could not prepare FFmpeg diagnostics: {exc}",
            command_id=command_id,
            command=cmd,
            exit_code=None,
            duration_seconds=time.perf_counter() - started,
            stderr_tail="",
            stderr_path=str(diagnostic_path),
        ) from exc

    stderr_tail = deque(maxlen=256)
    stdout_chunks: list[bytes] = []
    stdout_size = [0]
    stdout_overflow: list[str] = []
    stdout_reader_error: list[str] = []
    reader_error: list[str] = []
    output_limit_error: list[str] = []
    process: subprocess.Popen[str] | None = None
    watchdog_stop = threading.Event()
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if capture_stdout else None,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            **(
                {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
                if os.name == "nt"
                else {"start_new_session": True}
            ),
        )

        def watchdog_output_size() -> None:
            try:
                from .media_limits import MediaLimits
                max_bytes = int(MediaLimits.from_environment().max_output_bytes)
            except (TypeError, ValueError):
                return
            output_path = None
            if cmd:
                candidate = str(cmd[-1])
                if candidate and not candidate.startswith("-") and not candidate.startswith("pipe:"):
                    output_path = Path(candidate)
            if output_path is None:
                return
            while not watchdog_stop.wait(0.25):
                try:
                    if output_path.is_file() and output_path.stat().st_size > max_bytes:
                        output_limit_error.append(
                            f"FFmpeg output exceeded {max_bytes} bytes: {output_path}"
                        )
                        terminate_process_tree(process, grace_seconds=0.5)
                        return
                except OSError:
                    continue

        def pump_stderr() -> None:
            stream = process.stderr
            if stream is None:
                return
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    stderr_tail.append(chunk)
                    try:
                        diagnostic_handle.write(chunk)
                        diagnostic_handle.flush()
                    except OSError as exc:
                        reader_error.append(str(exc))
                    if mirror_stderr:
                        sys.stderr.write(chunk)
                        sys.stderr.flush()
            finally:
                stream.close()

        def pump_stdout() -> None:
            stream = process.stdout
            if stream is None:
                return
            try:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    encoded = chunk.encode("utf-8", errors="replace")
                    available = stdout_limit - stdout_size[0]
                    if len(encoded) > available:
                        if available > 0:
                            stdout_chunks.append(encoded[:available])
                            stdout_size[0] += available
                        stdout_overflow.append(
                            f"FFmpeg stdout exceeded configured capture limit of {stdout_limit} bytes"
                        )
                        terminate_process_tree(process, grace_seconds=0.5)
                        return
                    stdout_chunks.append(encoded)
                    stdout_size[0] += len(encoded)
            except OSError as exc:
                stdout_reader_error.append(str(exc))
            finally:
                stream.close()

        reader = threading.Thread(
            target=pump_stderr,
            name="aivf-ffmpeg-stderr",
            daemon=True,
        )
        stdout_reader = (
            threading.Thread(
                target=pump_stdout,
                name="aivf-ffmpeg-stdout",
                daemon=True,
            )
            if capture_stdout
            else None
        )
        watchdog = threading.Thread(
            target=watchdog_output_size,
            name="aivf-ffmpeg-output-watchdog",
            daemon=True,
        )
        reader.start()
        if stdout_reader is not None:
            stdout_reader.start()
        watchdog.start()
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_process_tree(process, grace_seconds=2.0)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            watchdog_stop.set()
            reader.join(timeout=2)
            if stdout_reader is not None:
                stdout_reader.join(timeout=2)
            watchdog.join(timeout=1)
            detail = "".join(stderr_tail).strip()
            detail_suffix = f": {detail[-2000:]}" if detail else ""
            raise FFmpegExecutionError(
                f"FFmpeg timed out after {timeout}s{detail_suffix}; diagnostics={diagnostic_path}",
                command_id=command_id,
                command=cmd,
                exit_code=None,
                duration_seconds=time.perf_counter() - started,
                stderr_tail=detail[-2000:],
                stderr_path=str(diagnostic_path),
            ) from exc

        watchdog_stop.set()
        reader.join(timeout=2)
        if stdout_reader is not None:
            stdout_reader.join(timeout=2)
        watchdog.join(timeout=1)
        detail = "".join(stderr_tail).strip()
        if output_limit_error:
            raise FFmpegExecutionError(
                output_limit_error[-1],
                command_id=command_id,
                command=cmd,
                exit_code=returncode,
                duration_seconds=time.perf_counter() - started,
                stderr_tail=detail[-2000:],
                stderr_path=str(diagnostic_path),
            )
        if stdout_overflow:
            raise FFmpegExecutionError(
                stdout_overflow[-1],
                command_id=command_id,
                command=cmd,
                exit_code=returncode,
                duration_seconds=time.perf_counter() - started,
                stderr_tail=detail[-2000:],
                stderr_path=str(diagnostic_path),
            )
        if returncode != 0:
            detail_suffix = f": {detail[-2000:]}" if detail else ""
            if reader_error:
                detail_suffix += f"; diagnostic_write_error={reader_error[-1]}"
            raise FFmpegExecutionError(
                f"FFmpeg failed with exit code {returncode}{detail_suffix}; diagnostics={diagnostic_path}",
                command_id=command_id,
                command=cmd,
                exit_code=returncode,
                duration_seconds=time.perf_counter() - started,
                stderr_tail=detail[-2000:],
                stderr_path=str(diagnostic_path),
            )
        if reader_error:
            raise FFmpegExecutionError(
                f"FFmpeg diagnostics write failed: {reader_error[-1]}; diagnostics={diagnostic_path}",
                command_id=command_id,
                command=cmd,
                exit_code=returncode,
                duration_seconds=time.perf_counter() - started,
                stderr_tail=detail[-2000:],
                stderr_path=str(diagnostic_path),
            )
        if stdout_reader_error:
            raise FFmpegExecutionError(
                f"FFmpeg stdout capture failed: {stdout_reader_error[-1]}",
                command_id=command_id,
                command=cmd,
                exit_code=returncode,
                duration_seconds=time.perf_counter() - started,
                stderr_tail=detail[-2000:],
                stderr_path=str(diagnostic_path),
            )
        stdout_value = (
            b"".join(stdout_chunks).decode("utf-8", errors="replace")
            if capture_stdout
            else None
        )
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout_value, stderr=detail
        )
    except FFmpegExecutionError:
        raise
    except BaseException:
        if process is not None and process.poll() is None:
            terminate_process_tree(process, grace_seconds=1.0)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        raise
    finally:
        watchdog_stop.set()
        diagnostic_handle.close()

def run_ffmpeg(
    cmd: List[str],
    timeout: Optional[int] = None,
    capture_output: bool = False,
    diagnostics_dir: str | Path | None = None,
) -> subprocess.CompletedProcess:
    cmd = _validate_tool_argv(cmd, "ffmpeg")
    cmd[0] = _ffmpeg_binary()
    timeout = timeout if timeout is not None else _bounded_timeout("AIVF_FFMPEG_TIMEOUT_SECONDS", 3600)
    try:
        with slot():
            # Captured analysis commands also use streamed, bounded capture so
            # timeouts terminate the full process tree and cannot buffer huge logs.
            return _run_ffmpeg_streaming(
                cmd,
                timeout,
                diagnostics_dir,
                capture_stdout=capture_output,
                mirror_stderr=not capture_output,
            )
    except FFmpegExecutionError:
        raise
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        if len(detail) > 2000:
            detail = detail[-2000:]
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"FFmpeg failed with exit code {exc.returncode}{suffix}") from exc


def render_segment(src_clip: str, ss: float, duration: float, vf: str, dst: str) -> None:
    if duration <= 0:
        raise ValueError("render duration must be positive")
    src_clip = _validate_media_input(src_clip, "source clip")
    encoder = choose_encoder()
    candidates = [encoder, "libx264"] if encoder in ("h264_nvenc", "hevc_nvenc", "h264_amf", "h264_vaapi") else ["libx264"]
    last_error = None
    seek_start = 0.0 if Path(src_clip).parent.name == "_clips" else max(0.0, ss)
    for selected in candidates:
        if selected in ("h264_nvenc", "hevc_nvenc"):
            extra = ["-preset", "p5", "-rc", "vbr_hq", "-b:v", "6000k"]
        elif selected == "h264_amf":
            extra = ["-quality", "quality", "-b:v", "6000k"]
        elif selected == "h264_vaapi":
            extra = ["-qp", "23"]
        else:
            extra = ["-preset", "fast", "-crf", "23"]
        vf_full = f"{vf},tpad=stop_mode=clone:stop_duration={float(duration):.3f}"
        if selected == "h264_vaapi":
            vf_full = f"{vf_full},format=nv12,hwupload"
        cmd = ["ffmpeg", "-y"]
        if selected == "h264_vaapi":
            cmd.extend(["-vaapi_device", "/dev/dri/renderD128"])
        cmd.extend([
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
        ])
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
    if "nvenc" in codec:
        opts = ["-preset", preset.get("preset", "p5"), "-rc", preset.get("rc", "vbr_hq"), "-b:v", preset.get("bitrate", "6000k")]
    elif codec == "h264_amf":
        opts = ["-quality", preset.get("quality", "quality"), "-b:v", preset.get("bitrate", "6000k")]
    elif codec == "h264_vaapi":
        opts = ["-qp", preset.get("qp", "23")]
    else:
        opts = ["-preset", preset.get("preset", "slow"), "-crf", preset.get("crf", "20")]
    cmd = ["ffmpeg", "-y"]
    if codec == "h264_vaapi":
        cmd += ["-vaapi_device", preset.get("device", "/dev/dri/renderD128")]
    cmd += [
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list_path,
        "-vf",
        "format=nv12,hwupload" if codec == "h264_vaapi" else "null",
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
        if codec not in {"h264_nvenc", "hevc_nvenc", "h264_vaapi", "h264_amf"}:
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


def stamp_media_metadata(path: str, *, version: str = "3.0.0") -> str:
    """Embed a traceability tag without re-encoding the media streams."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    version = str(version).strip()[:64]
    if not version:
        raise ValueError("version must not be empty")
    temp = target.with_name(f".{target.stem}.metadata{target.suffix}")
    try:
        run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i",
                str(target),
                "-map",
                "0",
                "-c",
                "copy",
                "-metadata",
                f"comment=Edit Factory {version}",
                "-movflags",
                "+faststart",
                str(temp),
            ],
            timeout=300,
        )
        validate_media_output(str(temp), require_video=True, require_audio=False)
        os.replace(temp, target)
        return str(target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise


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
    """Overlay voiceover on the existing program audio without dropping it."""
    video_path = _validate_media_input(video_path, "voiceover video")
    vo_path = _validate_media_input(vo_path, "voiceover audio")
    source = validate_media_output(video_path, require_video=True, require_audio=False)
    has_audio = any(stream.get("codec_type") == "audio" for stream in source.get("streams", []))
    duration = float((source.get("format") or {}).get("duration") or 0.0)
    if duration <= 0:
        raise RuntimeError("voiceover source video has no positive duration")
    output = _validate_media_path(output_path)

    if has_audio:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-i",
            vo_path,
            "-filter_complex",
            "[0:a:0]volume=0.35[program];"
            "[1:a:0]apad,atrim=duration=" + f"{duration:.3f}" + "[voice];"
            "[program][voice]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{duration:.3f}",
            "-movflags",
            "+faststart",
            output,
        ]
    else:
        # Preserve source duration when the video has no program audio.
        # apad fills a short voiceover; the explicit -t trims any overrun.
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_path,
            "-i",
            vo_path,
            "-filter_complex",
            "[1:a:0]apad[aout]",
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{duration:.3f}",
            "-movflags",
            "+faststart",
            output,
        ]
    run_ffmpeg(cmd)
    validate_media_output(output, require_video=True, require_audio=True)
