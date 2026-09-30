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

def _numeric_measurement(
    payload: dict[str, Any],
    key: str,
    *,
    allow_negative_infinity: bool = False,
) -> float:
    value = payload.get(key)
    if value is None:
        raise AudioNormalizationError(f"FFmpeg loudnorm measurement {key} is missing")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise AudioNormalizationError(f"FFmpeg loudnorm measurement {key} is not numeric") from exc
    if math.isnan(result) or (
        math.isinf(result)
        and not (allow_negative_infinity and result < 0)
    ):
        raise AudioNormalizationError(f"FFmpeg loudnorm measurement {key} is not finite")
    return result


def measure_loudness(path: str | Path) -> dict[str, Any]:
    result = run_ffmpeg(
        ["ffmpeg","-hide_banner","-i",str(path),"-af","loudnorm=I=-14:TP=-1.0:LRA=11:print_format=json","-f","null","-"],
        timeout=900, capture_output=True,
    )
    payload = _last_json(result.stderr or "")
    integrated_lufs = _numeric_measurement(payload, "input_i", allow_negative_infinity=True)
    true_peak = _numeric_measurement(payload, "input_tp", allow_negative_infinity=True)
    loudness_range = _numeric_measurement(payload, "input_lra", allow_negative_infinity=True)
    return {
        "integrated_lufs": integrated_lufs,
        "true_peak": true_peak,
        "loudness_range": loudness_range,
        "is_silent": integrated_lufs == float("-inf") or integrated_lufs < -60.0,
        "measured": payload,
    }

def normalize_loudness(
    input_path: str | Path,
    output_path: str | Path,
    *,
    target_i: float = -14.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
) -> str:
    for value, name in ((target_i, "target_i"), (target_tp, "target_tp"), (target_lra, "target_lra")):
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite")
    source, target = Path(input_path), Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(source)

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=".aivf-loudnorm-", suffix=".mp4", dir=target.parent
    )
    os.close(fd)
    temp = Path(temp_name)

    try:
        try:
            measurement = measure_loudness(source)
        except Exception as exc:
            raise AudioNormalizationError(f"loudness measurement failed: {exc}") from exc

        measured = measurement["measured"]
        parts = [
            f"loudnorm=I={float(target_i):.1f}",
            f"TP={float(target_tp):.1f}",
            f"LRA={float(target_lra):.1f}",
        ]
        for key in ("measured_I", "measured_TP", "measured_LRA", "measured_thresh", "offset"):
            value = measured.get(key)
            if value not in (None, "", "inf", "-inf"):
                parts.append(f"{key}={value}")
        parts.append("linear=false:print_format=summary")

        source_info = validate_media_output(
            str(source), require_video=True, require_audio=True
        )
        run_ffmpeg(
            [
                "ffmpeg", "-hide_banner", "-y", "-i", str(source),
                "-c:v", "copy", "-af", ":".join(parts),
                "-c:a", "aac", "-b:a", "192k", str(temp),
            ],
            timeout=1800,
            capture_output=True,
        )

        if not temp.is_file() or temp.stat().st_size <= 0:
            raise AudioNormalizationError("normalized output is missing or empty")

        normalized_info = validate_media_output(
            str(temp), require_video=True, require_audio=True
        )
        source_duration = float((source_info.get("format") or {}).get("duration") or 0.0)
        normalized_duration = float((normalized_info.get("format") or {}).get("duration") or 0.0)
        if source_duration <= 0 or normalized_duration <= 0:
            raise AudioNormalizationError("normalized output has invalid duration")
        if abs(normalized_duration - source_duration) > 0.5:
            raise AudioNormalizationError("normalized output duration changed unexpectedly")

        final_loudness = measure_loudness(temp)
        integrated = float(final_loudness["integrated_lufs"])
        true_peak = float(final_loudness["true_peak"])
        lra = float(final_loudness["loudness_range"])
        if not bool(final_loudness.get("is_silent")):
            if abs(integrated - float(target_i)) > 0.75:
                raise AudioNormalizationError(
                    f"normalized integrated loudness {integrated:.2f} LUFS is outside target {float(target_i):.2f} ±0.75 LU"
                )
            if true_peak > float(target_tp) + 0.2:
                raise AudioNormalizationError(
                    f"normalized true peak {true_peak:.2f} dBTP exceeds target {float(target_tp):.2f} dBTP +0.2"
                )
            if lra > float(target_lra) + 3.0:
                raise AudioNormalizationError(
                    f"normalized loudness range {lra:.2f} LU exceeds target {float(target_lra):.2f} LU +3"
                )

        os.replace(temp, target)
    except Exception as exc:
        temp.unlink(missing_ok=True)
        if isinstance(exc, AudioNormalizationError):
            raise
        raise AudioNormalizationError(f"audio normalization failed: {exc}") from exc
    return str(target)
__all__ = ["AudioNormalizationError","measure_loudness","normalize_loudness"]
