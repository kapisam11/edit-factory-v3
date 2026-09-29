"""Two-pass EBU R128 loudness normalization helpers."""
from __future__ import annotations
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any
from .render_engine import run_ffmpeg, validate_media_output

class AudioNormalizationError(RuntimeError):
    pass

def _last_json(text: str) -> dict[str, Any]:
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AudioNormalizationError("FFmpeg loudnorm did not return measurement JSON")
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AudioNormalizationError("FFmpeg loudnorm returned malformed JSON") from exc
    if not isinstance(payload, dict):
        raise AudioNormalizationError("FFmpeg loudnorm JSON was not an object")
    return payload

def measure_loudness(path: str | Path) -> dict[str, Any]:
    result = run_ffmpeg(
        ["ffmpeg","-hide_banner","-i",str(path),"-af","loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json","-f","null","-"],
        timeout=900, capture_output=True,
    )
    payload = _last_json(result.stderr or "")
    return {
        "integrated_lufs": payload.get("input_i"),
        "true_peak": payload.get("input_tp"),
        "loudness_range": payload.get("input_lra"),
        "measured": payload,
    }

def normalize_loudness(input_path: str | Path, output_path: str | Path, *, target_i: float = -14.0, target_tp: float = -1.0, target_lra: float = 11.0) -> str:
    for value, name in ((target_i,"target_i"),(target_tp,"target_tp"),(target_lra,"target_lra")):
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    source, target = Path(input_path), Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    measurement = measure_loudness(source)
    measured = measurement["measured"]
    parts = [f"loudnorm=I={float(target_i):.1f}", f"TP={float(target_tp):.1f}", f"LRA={float(target_lra):.1f}"]
    for key in ("measured_I","measured_TP","measured_LRA","measured_thresh","offset"):
        value = measured.get(key)
        if value not in (None,"","inf","-inf"):
            parts.append(f"{key}={value}")
    parts.append("linear=false:print_format=summary")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".aivf-loudnorm-", suffix=".mp4", dir=target.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        run_ffmpeg(["ffmpeg","-hide_banner","-y","-i",str(source),"-c:v","copy","-af",":".join(parts),"-c:a","aac","-b:a","192k",str(temp)], timeout=1800, capture_output=True)
        if not temp.is_file() or temp.stat().st_size <= 0:
            raise AudioNormalizationError("normalized output is missing or empty")
        os.replace(temp, target)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        if isinstance(exc, AudioNormalizationError):
            raise
        raise AudioNormalizationError(f"audio normalization failed: {exc}") from exc
    return str(target)

__all__ = ["AudioNormalizationError","measure_loudness","normalize_loudness"]
