"""Centralized hard media admission and output limits.

The policy is intentionally conservative and environment-configurable. Validation
happens before expensive media work and again after rendering.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from typing import Any, Sequence

from .render_engine import run_ffprobe


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _float_env(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return value


@dataclass(frozen=True)
class MediaLimits:
    max_input_bytes: int = 2 * 1024**3
    max_duration_seconds: float = 900.0
    max_width: int = 3840
    max_height: int = 2160
    max_fps: float = 120.0
    max_audio_channels: int = 8
    max_output_bytes: int = 5 * 1024**3
    max_job_seconds: float = 1800.0
    min_free_disk_bytes: int = 20 * 1024**3

    @classmethod
    def from_environment(cls) -> "MediaLimits":
        return cls(
            max_input_bytes=_int_env("AIVF_MAX_INPUT_BYTES", cls.max_input_bytes),
            max_duration_seconds=_float_env("AIVF_MAX_INPUT_DURATION_SECONDS", cls.max_duration_seconds, 1.0),
            max_width=_int_env("AIVF_MAX_INPUT_WIDTH", cls.max_width),
            max_height=_int_env("AIVF_MAX_INPUT_HEIGHT", cls.max_height),
            max_fps=_float_env("AIVF_MAX_INPUT_FPS", cls.max_fps, 1.0),
            max_audio_channels=_int_env("AIVF_MAX_AUDIO_CHANNELS", cls.max_audio_channels),
            max_output_bytes=_int_env("AIVF_MAX_OUTPUT_BYTES", cls.max_output_bytes),
            max_job_seconds=_float_env("AIVF_MAX_JOB_SECONDS", cls.max_job_seconds, 1.0),
            min_free_disk_bytes=_int_env("AIVF_MIN_FREE_DISK_BYTES", cls.min_free_disk_bytes),
        )


def _fps(stream: dict[str, Any]) -> float:
    raw = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1")
    try:
        numerator, denominator = raw.split("/", 1)
        value = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def _validate_container_signature(path: Path) -> None:
    """Reject extension-only lookalikes before invoking expensive media processing."""
    suffix = path.suffix.lower()
    with path.open("rb") as handle:
        header = handle.read(16)
    if suffix in {".mp4", ".m4v"}:
        valid = len(header) >= 8 and header[4:8] == b"ftyp"
    elif suffix == ".mov":
        atom = header[4:8] if len(header) >= 8 else b""
        valid = atom == b"ftyp" or atom in {b"moov", b"mdat", b"wide", b"free", b"skip"}
    elif suffix == ".avi":
        valid = len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI "
    elif suffix in {".mkv", ".webm"}:
        valid = header.startswith(bytes.fromhex("1A45DFA3"))
    else:
        valid = bool(header)
    if not valid:
        raise ValueError("media input container signature does not match its extension")


def estimate_resource_budget(
    media: dict[str, Any],
    *,
    target_seconds: float,
    limits: MediaLimits | None = None,
) -> dict[str, int | float]:
    """Estimate conservative disk/CPU/memory reservations for one media job."""
    limits = limits or MediaLimits.from_environment()
    pixels_per_second = max(
        1.0,
        float(media.get("width", 0))
        * float(media.get("height", 0))
        * max(1.0, float(media.get("fps", 0))),
    )
    baseline = 1920.0 * 1080.0 * 30.0
    cpu_weight = max(0.5, min(4.0, pixels_per_second / baseline))
    gib = 1024**3
    width = max(1.0, float(media.get("width", 0)))
    height = max(1.0, float(media.get("height", 0)))
    fps = max(1.0, float(media.get("fps", 0)))
    pixels_scale = max(1.0, (width * height) / (1920.0 * 1080.0))
    # Rendering/AI memory is dominated by frame buffers, codec surfaces, Python/ML
    # state, and subprocess trees rather than raw file dimensions alone. Reserve
    # explicit headroom for the requested AI-heavy features so admission reflects
    # the actual process shape instead of under-counting a 1080p render as ~8 MB.
    memory_bytes = 2 * gib
    memory_bytes += int(min(2 * gib, pixels_scale * gib))
    job_class = str(media.get("job_class") or "cpu_render").strip().lower()
    if job_class == "ai_heavy":
        memory_bytes += 4 * gib
    if bool(media.get("enable_ocr")):
        memory_bytes += 2 * gib
    if bool(media.get("enable_object_detection")):
        memory_bytes += 1 * gib
    if bool(media.get("enable_diarization")):
        memory_bytes += 4 * gib
    if fps > 60:
        memory_bytes += 1 * gib
    memory_bytes = int(max(512 * 1024**2, min(12 * gib, memory_bytes)))
    input_bytes = int(media["size_bytes"])
    reserved_bytes = int(
        min(
            limits.max_output_bytes,
            max(512 * 1024**2, input_bytes * 3, int(float(target_seconds) * 8 * 1024**2)),
        )
    )
    return {
        "input_bytes": input_bytes,
        "reserved_bytes": reserved_bytes,
        "cpu_weight": round(cpu_weight, 3),
        "memory_bytes": memory_bytes,
    }


def probe_media_contract(
    path: str | Path,
    limits: MediaLimits | None = None,
    *,
    enforce_input_size: bool = True,
) -> dict[str, Any]:
    limits = limits or MediaLimits.from_environment()
    target = Path(path)
    if not target.is_file():
        raise ValueError(f"media input does not exist: {target}")
    size = target.stat().st_size
    if size <= 0:
        raise ValueError("media input is empty")
    _validate_container_signature(target)
    if enforce_input_size and size > limits.max_input_bytes:
        raise ValueError(
            f"media input exceeds {limits.max_input_bytes} bytes: {size}"
        )
    result = run_ffprobe(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "format=duration,size,format_name",
            "-show_streams",
            "-of", "json",
            str(target),
        ],
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(f"ffprobe rejected media input: {(result.stderr or '')[-1000:]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe returned invalid JSON") from exc

    streams: Sequence[dict[str, Any]] = payload.get("streams") or ()
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not videos:
        raise ValueError("media input has no video stream")
    video = videos[0]
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    duration = float(
        video.get("duration")
        or (payload.get("format") or {}).get("duration")
        or 0.0
    )
    fps = _fps(video)
    if width <= 0 or height <= 0:
        raise ValueError("media input has invalid video dimensions")
    if width > limits.max_width or height > limits.max_height:
        raise ValueError(
            f"media dimensions exceed {limits.max_width}x{limits.max_height}: {width}x{height}"
        )
    if duration <= 0 or duration > limits.max_duration_seconds:
        raise ValueError(
            f"media duration must be >0 and <= {limits.max_duration_seconds:g}s"
        )
    if fps > limits.max_fps:
        raise ValueError(f"media frame rate exceeds {limits.max_fps:g} fps")
    channels = max([int(stream.get("channels") or 0) for stream in audios] or [0])
    if channels > limits.max_audio_channels:
        raise ValueError(
            f"audio channel count exceeds {limits.max_audio_channels}: {channels}"
        )

    return {
        "path": str(target),
        "size_bytes": size,
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "audio_channels": channels,
        "format": (payload.get("format") or {}).get("format_name"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audios[0].get("codec_name") if audios else None,
    }


def validate_render_output(
    path: str | Path,
    *,
    limits: MediaLimits | None = None,
    expected_duration_seconds: float | None = None,
) -> dict[str, Any]:
    limits = limits or MediaLimits.from_environment()
    target = Path(path)
    if not target.is_file() or target.stat().st_size <= 0:
        raise ValueError(f"render output missing or empty: {target}")
    if target.stat().st_size > limits.max_output_bytes:
        raise ValueError(
            f"render output exceeds {limits.max_output_bytes} bytes: {target.stat().st_size}"
        )
    report = probe_media_contract(target, limits, enforce_input_size=False)
    if expected_duration_seconds is not None:
        duration = float(report["duration_seconds"])
        tolerance = max(0.25, float(expected_duration_seconds) * 0.02)
        if abs(duration - float(expected_duration_seconds)) > tolerance:
            raise ValueError(
                f"render duration {duration:.3f}s diverges from expected {float(expected_duration_seconds):.3f}s"
            )
    return report


__all__ = ["MediaLimits", "probe_media_contract", "validate_render_output", "estimate_resource_budget"]
