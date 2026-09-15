"""Strict post-render quality and contract verification for Edit Factory v3."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any, Dict, Mapping, Sequence


class RenderContractError(RuntimeError):
    """Raised when a rendered artifact violates the requested production contract."""


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def probe_media(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path) or os.path.getsize(path) <= 0:
        raise RenderContractError(f"rendered media is missing or empty: {path}")
    try:
        result = _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,size",
                "-show_streams",
                "-of",
                "json",
                path,
            ]
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RenderContractError(f"ffprobe could not inspect rendered media: {exc}") from exc

    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise RenderContractError("rendered artifact contains no video stream")
    duration = float(payload.get("format", {}).get("duration") or 0.0)
    return {
        "duration": duration,
        "size": int(payload.get("format", {}).get("size") or os.path.getsize(path)),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/0",
        "has_audio": audio is not None,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
    }


def normalize_duration(input_path: str, target_path: str, target_seconds: float) -> str:
    """Make the final container exactly the requested duration using FFmpeg."""
    info = probe_media(input_path)
    current = float(info["duration"])
    delta = float(target_seconds) - current
    if abs(delta) <= 0.05:
        if os.path.abspath(input_path) != os.path.abspath(target_path):
            with open(input_path, "rb") as src, open(target_path, "wb") as dst:
                dst.write(src.read())
        return target_path

    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    fd, temp_path = tempfile.mkstemp(suffix=".mp4", dir=os.path.dirname(target_path) or ".")
    os.close(fd)
    try:
        video_filter = None
        audio_filter = None
        if delta > 0:
            video_filter = f"tpad=stop_mode=clone:stop_duration={delta:.3f}"
            audio_filter = "apad"
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", input_path]
        if video_filter:
            command += ["-vf", video_filter]
        if audio_filter:
            command += ["-af", audio_filter]
        command += [
            "-t",
            f"{float(target_seconds):.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            temp_path,
        ]
        _run(command)
        normalized = probe_media(temp_path)
        if abs(normalized["duration"] - float(target_seconds)) > 0.08:
            raise RenderContractError(
                f"duration normalization failed: {normalized['duration']:.3f}s != {target_seconds:.3f}s"
            )
        os.replace(temp_path, target_path)
        return target_path
    except (OSError, subprocess.CalledProcessError, RenderContractError) as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise RenderContractError(f"could not normalize rendered duration: {exc}") from exc


def _sample_visual_changes(path: str, sample_hz: float = 2.0) -> Dict[str, Any]:
    """Measure coarse frame-to-frame change without requiring OpenCV."""
    import numpy as np

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        path,
        "-vf",
        f"fps={sample_hz:g},scale=160:90,format=gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RenderContractError(f"could not sample rendered frames: {exc}") from exc

    frame_size = 160 * 90
    raw = np.frombuffer(result.stdout, dtype=np.uint8)
    frame_count = raw.size // frame_size
    if frame_count < 2:
        return {"frames": frame_count, "change_events": [], "max_gap": None}

    frames = raw[: frame_count * frame_size].reshape(frame_count, frame_size).astype(np.float32)
    deltas = np.mean(np.abs(frames[1:] - frames[:-1]), axis=1)
    threshold = max(7.0, float(np.percentile(deltas, 65)) * 0.75)
    change_events = [
        round((idx + 1) / sample_hz, 3)
        for idx, delta in enumerate(deltas)
        if delta >= threshold
    ]
    return {
        "frames": frame_count,
        "threshold": round(threshold, 3),
        "change_events": change_events,
        "max_gap": None,
    }


def strict_render_check(
    path: str,
    *,
    target_seconds: float,
    platform_profile: Mapping[str, Any],
    retention_events: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    info = probe_media(path)
    expected_width = int(platform_profile.get("width") or 0)
    expected_height = int(platform_profile.get("height") or 0)
    errors = []
    warnings = []

    if abs(info["duration"] - float(target_seconds)) > 0.08:
        errors.append(
            f"duration {info['duration']:.3f}s does not match target {float(target_seconds):.3f}s"
        )
    if expected_width and info["width"] != expected_width:
        errors.append(f"width {info['width']} != required {expected_width}")
    if expected_height and info["height"] != expected_height:
        errors.append(f"height {info['height']} != required {expected_height}")
    if not info["has_audio"]:
        warnings.append("rendered artifact has no audio stream")

    visual = _sample_visual_changes(path)
    events = [float(item.get("time", 0.0)) for item in retention_events if isinstance(item, Mapping)]
    if events:
        if events[0] > 0.25:
            warnings.append("retention map does not begin near the opening")
        if any(b <= a for a, b in zip(events, events[1:])):
            errors.append("retention map contains non-increasing event times")
        if any(t > float(target_seconds) + 0.05 for t in events):
            errors.append("retention map contains events beyond target duration")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "media": info,
        "visual_sampling": visual,
    }
