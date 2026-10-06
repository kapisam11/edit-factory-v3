"""Centralized hard media and resource admission policy for Edit Factory."""
from __future__ import annotations
from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping
from .production_guardrails import GuardrailError, run_tool

def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value

@dataclass(frozen=True)
class MediaLimits:
    max_input_bytes: int = 2 * 1024**3
    max_duration_seconds: int = 900
    max_width: int = 3840
    max_height: int = 2160
    max_fps: int = 120
    max_audio_channels: int = 8
    max_output_bytes: int = 5 * 1024**3
    max_job_seconds: int = 1800
    min_free_disk_bytes: int = 20 * 1024**3
    resource_capacity_units: int = 100
    max_reserved_memory_bytes: int = 8 * 1024**3

    @classmethod
    def from_environment(cls) -> "MediaLimits":
        return cls(
            max_input_bytes=_int_env("AIVF_MEDIA_MAX_INPUT_BYTES", cls.max_input_bytes, 1, 16 * 1024**3),
            max_duration_seconds=_int_env("AIVF_MEDIA_MAX_DURATION_SECONDS", cls.max_duration_seconds, 1, 86400),
            max_width=_int_env("AIVF_MEDIA_MAX_WIDTH", cls.max_width, 64, 16384),
            max_height=_int_env("AIVF_MEDIA_MAX_HEIGHT", cls.max_height, 64, 16384),
            max_fps=_int_env("AIVF_MEDIA_MAX_FPS", cls.max_fps, 1, 240),
            max_audio_channels=_int_env("AIVF_MEDIA_MAX_AUDIO_CHANNELS", cls.max_audio_channels, 1, 32),
            max_output_bytes=_int_env("AIVF_MEDIA_MAX_OUTPUT_BYTES", cls.max_output_bytes, 1, 50 * 1024**3),
            max_job_seconds=_int_env("AIVF_MEDIA_MAX_JOB_SECONDS", cls.max_job_seconds, 1, 86400),
            min_free_disk_bytes=_int_env("AIVF_MEDIA_MIN_FREE_DISK_BYTES", cls.min_free_disk_bytes, 256 * 1024**2, 100 * 1024**4),
            resource_capacity_units=_int_env("AIVF_RESOURCE_CAPACITY_UNITS", cls.resource_capacity_units, 1, 100000),
            max_reserved_memory_bytes=_int_env("AIVF_MAX_RESERVED_MEMORY_BYTES", cls.max_reserved_memory_bytes, 256 * 1024**2, 128 * 1024**3),
        )

DEFAULT_MEDIA_LIMITS = MediaLimits.from_environment()

def _fps(rate: object) -> float:
    try:
        return float(Fraction(str(rate or "0/0")))
    except (ValueError, ZeroDivisionError):
        return 0.0

def _summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    streams = [item for item in payload.get("streams", []) if isinstance(item, Mapping)]
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    fmt = payload.get("format") if isinstance(payload.get("format"), Mapping) else {}
    return {
        "duration": float(fmt.get("duration") or 0.0),
        "size": int(fmt.get("size") or 0),
        "bit_rate": int(fmt.get("bit_rate") or 0),
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "fps": _fps((video or {}).get("avg_frame_rate") or (video or {}).get("r_frame_rate")),
        "format_name": str(fmt.get("format_name") or ""),
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "audio_channels": int((audio or {}).get("channels") or 0),
        "has_video": video is not None,
        "has_audio": audio is not None,
    }

def probe_media_file(path: str | Path, *, timeout: int = 60) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file() or source.stat().st_size <= 0:
        raise GuardrailError(f"media file is missing or empty: {source}")
    result = run_tool([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,size,format_name,bit_rate",
        "-show_streams", "-of", "json", str(source),
    ], timeout=timeout, stderr_limit=12000)
    if result.returncode != 0:
        raise GuardrailError(f"ffprobe rejected media input: {result.stderr_tail[-2000:]}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise GuardrailError("ffprobe returned invalid media JSON") from exc
    if not isinstance(payload, Mapping):
        raise GuardrailError("ffprobe returned an invalid media payload")
    return {"probe": dict(payload), "summary": _summary(payload)}

def _magic_matches(path: Path, suffix: str) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError:
        return False
    suffix = str(suffix).lower()
    if suffix in {".mp4", ".mov"}:
        return len(header) >= 12 and header[4:8] == b"ftyp"
    if suffix == ".avi":
        return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI "
    if suffix in {".mkv", ".webm"}:
        return header[:4] == bytes((0x1A, 0x45, 0xDF, 0xA3))
    return False

def validate_input_file(path: str | Path, *, suffix: str, limits: MediaLimits | None = None) -> dict[str, Any]:
    policy = limits or DEFAULT_MEDIA_LIMITS
    source = Path(path)
    size = source.stat().st_size
    if size <= 0:
        raise GuardrailError("media input is empty")
    if size > policy.max_input_bytes:
        raise GuardrailError("media input exceeds the hard size limit")
    if not _magic_matches(source, suffix):
        raise GuardrailError("media input failed the container signature check")
    report = probe_media_file(source)
    summary = report["summary"]
    if not summary["has_video"]:
        raise GuardrailError("media input has no video stream")
    if not 0.01 <= summary["duration"] <= policy.max_duration_seconds:
        raise GuardrailError("media input duration exceeds the hard limit")
    if summary["width"] <= 0 or summary["height"] <= 0:
        raise GuardrailError("media input dimensions are invalid")
    if summary["width"] > policy.max_width or summary["height"] > policy.max_height:
        raise GuardrailError("media input resolution exceeds the hard limit")
    if summary["fps"] <= 0.0 or summary["fps"] > policy.max_fps:
        raise GuardrailError("media input frame rate exceeds the hard limit")
    if summary["audio_channels"] > policy.max_audio_channels:
        raise GuardrailError("media input audio channel count exceeds the hard limit")
    summary["size"] = size
    return summary

def validate_output_probe(path: str | Path, payload: Mapping[str, Any], *, limits: MediaLimits | None = None) -> dict[str, Any]:
    policy = limits or DEFAULT_MEDIA_LIMITS
    source = Path(path)
    if not source.is_file() or source.stat().st_size <= 0:
        raise GuardrailError("media output is missing or empty")
    if source.stat().st_size > policy.max_output_bytes:
        raise GuardrailError("media output exceeds the hard size limit")
    summary = _summary(payload)
    if not summary["has_video"]:
        raise GuardrailError("media output has no video stream")
    if not 0.01 <= summary["duration"] <= policy.max_duration_seconds:
        raise GuardrailError("media output duration exceeds the hard limit")
    if summary["width"] > policy.max_width or summary["height"] > policy.max_height:
        raise GuardrailError("media output resolution exceeds the hard limit")
    if summary["fps"] > policy.max_fps:
        raise GuardrailError("media output frame rate exceeds the hard limit")
    if summary["audio_channels"] > policy.max_audio_channels:
        raise GuardrailError("media output audio channel count exceeds the hard limit")
    summary["size"] = source.stat().st_size
    return summary

@dataclass(frozen=True)
class ResourceBudget:
    resource_units: int
    reserved_disk_bytes: int
    reserved_memory_bytes: int
    estimated_job_seconds: int

def estimate_resource_budget(summary: Mapping[str, Any], *, input_size_bytes: int | None = None, limits: MediaLimits | None = None) -> ResourceBudget:
    policy = limits or DEFAULT_MEDIA_LIMITS
    duration = max(0.25, float(summary.get("duration") or 0.0))
    width = max(64, int(summary.get("width") or 0))
    height = max(64, int(summary.get("height") or 0))
    fps = max(1.0, float(summary.get("fps") or 0.0))
    input_size = max(1, int(input_size_bytes if input_size_bytes is not None else summary.get("size") or 1))
    pixel_factor = (width * height) / float(1920 * 1080)
    fps_factor = fps / 30.0
    duration_factor = min(8.0, max(0.25, duration / 30.0))
    units = max(1, min(policy.resource_capacity_units, int(math.ceil(pixel_factor * fps_factor * duration_factor * 10.0))))
    memory = min(policy.max_reserved_memory_bytes, max(512 * 1024**2, width * height * 16))
    output_estimate = max(256 * 1024**2, int(max(input_size * 2.0, duration * 8 * 1024 * 1024)))
    reserved_disk = min(policy.max_output_bytes + input_size, input_size + output_estimate)
    estimated_seconds = max(30, min(policy.max_job_seconds, int(math.ceil(duration * (1 + pixel_factor * fps_factor)))))
    return ResourceBudget(units, reserved_disk, memory, estimated_seconds)

__all__ = ["DEFAULT_MEDIA_LIMITS","MediaLimits","ResourceBudget","estimate_resource_budget","probe_media_file","validate_input_file","validate_output_probe"]
