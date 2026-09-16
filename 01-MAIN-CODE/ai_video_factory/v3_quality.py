"""Strict post-render quality and contract verification for Edit Factory v3."""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
from typing import Any, Dict, Mapping, Optional, Sequence


class RenderContractError(RuntimeError):
    """Raised when a rendered artifact violates the requested production contract."""


def _timeout(env_name: str, default: int) -> int:
    try:
        value = int(os.environ.get(env_name, str(default)))
    except ValueError as exc:
        raise RenderContractError(f"{env_name} must be an integer") from exc
    if not 1 <= value <= 7200:
        raise RenderContractError(f"{env_name} must be between 1 and 7200 seconds")
    return value


def _run(command: Sequence[str], *, timeout: Optional[int] = None, capture_output: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(command), check=True, capture_output=capture_output, text=text,
                              timeout=timeout if timeout is not None else _timeout("AIVF_FFMPEG_TIMEOUT_SECONDS", 3600))
    except subprocess.TimeoutExpired as exc:
        raise RenderContractError(f"media command timed out after {exc.timeout}s") from exc
    except OSError as exc:
        raise RenderContractError(f"media command could not start: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else ""
        raise RenderContractError(f"media command failed with exit code {exc.returncode}: {stderr}") from exc


def _atomic_copy(input_path: str, target_path: str) -> str:
    directory = os.path.dirname(os.path.abspath(target_path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(suffix=".partial", dir=directory)
    try:
        with os.fdopen(fd, "wb") as dst, open(input_path, "rb") as src:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk: break
                dst.write(chunk)
            dst.flush(); os.fsync(dst.fileno())
        os.replace(temp_path, target_path); return target_path
    except BaseException:
        try: os.unlink(temp_path)
        except OSError: pass
        raise


def probe_media(path: str) -> Dict[str, Any]:
    if not os.path.isfile(path) or os.path.getsize(path) <= 0: raise RenderContractError(f"rendered media is missing or empty: {path}")
    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size,format_name,bit_rate", "-show_streams", "-of", "json", path],
                  timeout=_timeout("AIVF_FFPROBE_TIMEOUT_SECONDS", 30))
    try: payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc: raise RenderContractError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams") or []; video = next((s for s in streams if s.get("codec_type") == "video"), None); audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video: raise RenderContractError("rendered artifact contains no video stream")
    duration = float(payload.get("format", {}).get("duration") or 0.0); width = int(video.get("width") or 0); height = int(video.get("height") or 0)
    if duration <= 0 or width <= 0 or height <= 0: raise RenderContractError("rendered artifact has invalid duration or dimensions")
    return {"duration": duration, "size": int(payload.get("format", {}).get("size") or os.path.getsize(path)), "width": width, "height": height,
            "fps": video.get("r_frame_rate") or video.get("avg_frame_rate") or "0/0", "has_audio": audio is not None,
            "video_codec": video.get("codec_name"), "audio_codec": audio.get("codec_name") if audio else None,
            "format_name": payload.get("format", {}).get("format_name")}


def normalize_duration(input_path: str, target_path: str, target_seconds: float) -> str:
    if not math.isfinite(float(target_seconds)) or not 0.25 <= float(target_seconds) <= 180.0: raise RenderContractError("target duration must be between 0.25 and 180 seconds")
    info = probe_media(input_path); current = float(info["duration"]); delta = float(target_seconds) - current
    if abs(delta) <= 0.05: return _atomic_copy(input_path, target_path) if os.path.abspath(input_path) != os.path.abspath(target_path) else target_path
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True); fd, temp_path = tempfile.mkstemp(suffix=".mp4", dir=os.path.dirname(target_path) or "."); os.close(fd)
    try:
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", input_path]
        if delta > 0: command += ["-vf", f"tpad=stop_mode=clone:stop_duration={delta:.3f}", "-af", "apad"]
        command += ["-t", f"{float(target_seconds):.3f}", "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-movflags", "+faststart", temp_path]
        _run(command, timeout=_timeout("AIVF_FFMPEG_TIMEOUT_SECONDS", 3600)); normalized = probe_media(temp_path)
        if abs(normalized["duration"] - float(target_seconds)) > 0.08: raise RenderContractError(f"duration normalization failed: {normalized['duration']:.3f}s != {target_seconds:.3f}s")
        os.replace(temp_path, target_path); return target_path
    except BaseException as exc:
        try: os.unlink(temp_path)
        except OSError: pass
        if isinstance(exc, RenderContractError): raise
        raise RenderContractError(f"could not normalize rendered duration: {exc}") from exc


def enforce_retention_events(input_path: str, output_path: str, retention_events: Sequence[Mapping[str, Any]]) -> str:
    events = []; previous = -1.0
    for item in retention_events:
        if not isinstance(item, Mapping): raise RenderContractError("retention event must be an object")
        try: timestamp = float(item.get("time", -1.0))
        except (TypeError, ValueError) as exc: raise RenderContractError("retention event time is invalid") from exc
        if not math.isfinite(timestamp) or timestamp < 0.0 or timestamp <= previous: raise RenderContractError("retention events must be finite and strictly increasing")
        kind = str(item.get("kind", "motion")).strip().lower()
        if len(kind) > 64: raise RenderContractError("retention event kind is too long")
        events.append((timestamp, kind)); previous = timestamp
    if not events: raise RenderContractError("V3 retention map is empty")
    info = probe_media(input_path); kind_settings = {"zoom": (1.04, 0.045), "text": (1.02, 0.030), "motion": (1.05, 0.035), "angle": (1.06, 0.025), "clip": (1.08, 0.030), "beat drop": (1.14, 0.020)}; filters = []
    for timestamp, kind in events:
        if timestamp >= info["duration"] - 0.02: raise RenderContractError(f"retention event at {timestamp:.3f}s is outside rendered duration")
        contrast, brightness = kind_settings.get(kind, (1.04, 0.030)); start = max(0.0, timestamp - 0.04); end = min(info["duration"], timestamp + 0.16)
        filters.append(f"eq=contrast={contrast:.3f}:brightness={brightness:.3f}:enable='between(t,{start:.3f},{end:.3f})'")
    fd, temp_path = tempfile.mkstemp(suffix=".mp4", dir=os.path.dirname(output_path) or "."); os.close(fd)
    try:
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", input_path, "-vf", ",".join(filters), "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-movflags", "+faststart", temp_path], timeout=_timeout("AIVF_FFMPEG_TIMEOUT_SECONDS", 3600)); probe_media(temp_path); os.replace(temp_path, output_path); return output_path
    except BaseException as exc:
        try: os.unlink(temp_path)
        except OSError: pass
        if isinstance(exc, RenderContractError): raise
        raise RenderContractError(f"could not enforce retention events: {exc}") from exc


def _sample_visual_changes(path: str, sample_hz: float = 10.0) -> Dict[str, Any]:
    if not math.isfinite(sample_hz) or not 0.0 < sample_hz <= 30.0: raise ValueError("sample_hz must be > 0 and <= 30")
    info = probe_media(path); sample_duration = min(float(info["duration"]), 180.0)
    result = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-t", f"{sample_duration:.3f}", "-vf", f"fps={sample_hz:g},scale=160:90,format=gray", "-f", "rawvideo", "pipe:1"], timeout=_timeout("AIVF_FFMPEG_TIMEOUT_SECONDS", 3600), capture_output=True, text=False)
    frame_size = 160 * 90; raw = result.stdout if isinstance(result.stdout, (bytes, bytearray)) else b""; frame_count = len(raw) // frame_size
    if frame_count < 2: return {"frames": frame_count, "change_events": [], "max_gap": None}
    deltas = []
    for index in range(1, frame_count):
        previous = raw[(index - 1) * frame_size:index * frame_size]; current = raw[index * frame_size:(index + 1) * frame_size]; deltas.append(sum(abs(a - b) for a, b in zip(previous, current)) / frame_size)
    ordered = sorted(deltas); threshold = max(6.0, ordered[min(len(ordered) - 1, int(len(ordered) * 0.65))] * 0.70); change_events = [round((index + 1) / sample_hz, 3) for index, delta in enumerate(deltas) if delta >= threshold]
    max_gap = None
    if change_events:
        points = [0.0, *change_events, sample_duration]; max_gap = round(max(b - a for a, b in zip(points, points[1:])), 3)
    return {"frames": frame_count, "sample_hz": sample_hz, "sample_duration": sample_duration, "threshold": round(threshold, 3), "change_events": change_events, "max_gap": max_gap}


def strict_render_check(path: str, *, target_seconds: float, platform_profile: Mapping[str, Any], retention_events: Sequence[Mapping[str, Any]] = ()) -> Dict[str, Any]:
    info = probe_media(path); expected_width = int(platform_profile.get("width") or 0); expected_height = int(platform_profile.get("height") or 0); errors: list[str] = []; warnings: list[str] = []
    if abs(info["duration"] - float(target_seconds)) > 0.08: errors.append(f"duration {info['duration']:.3f}s does not match target {float(target_seconds):.3f}s")
    if expected_width and info["width"] != expected_width: errors.append(f"width {info['width']} != required {expected_width}")
    if expected_height and info["height"] != expected_height: errors.append(f"height {info['height']} != required {expected_height}")
    if not info["has_audio"]: warnings.append("rendered artifact has no audio stream")
    visual = _sample_visual_changes(path, sample_hz=10.0); events = []
    for item in retention_events:
        if isinstance(item, Mapping):
            try: events.append(float(item.get("time", 0.0)))
            except (TypeError, ValueError): errors.append("retention map contains a non-numeric event time")
    if not events: errors.append("retention map is empty")
    else:
        if events[0] > 0.25: errors.append("retention map does not begin near the opening")
        if any(b <= a for a, b in zip(events, events[1:])): errors.append("retention map contains non-increasing event times")
        if any(not math.isfinite(t) or t < 0.0 or t > float(target_seconds) + 0.05 for t in events): errors.append("retention map contains events outside target duration")
        detected = visual.get("change_events", []); missing = [event for event in events if not any(abs(event - change) <= 0.30 for change in detected)]
        if missing: errors.append("rendered visual stream is missing observable changes near retention events: " + ", ".join(f"{t:.2f}s" for t in missing[:8]))
    if visual.get("max_gap") is not None and visual["max_gap"] > 3.0: errors.append(f"coarse visual sampling found a change gap of {visual['max_gap']:.2f}s; maximum allowed gap is 3.00s")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "media": info, "visual_sampling": visual}
