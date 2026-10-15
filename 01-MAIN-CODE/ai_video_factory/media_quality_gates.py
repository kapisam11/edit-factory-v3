"""Objective, timestamped quality checks for upload-package media.

These checks can flag measurable defects. They do not claim to replace visual
review for caption placement, gameplay visibility, or voice/music balance.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Mapping


_SRT_TIME = re.compile(
    r"^(?P<h>\d{2,}):(?P<m>[0-5]\d):(?P<s>[0-5]\d),(?P<ms>\d{3})$"
)


def _srt_seconds(value: str) -> float | None:
    match = _SRT_TIME.fullmatch(str(value or "").strip())
    if not match:
        return None
    return (
        int(match.group("h")) * 3600
        + int(match.group("m")) * 60
        + int(match.group("s"))
        + int(match.group("ms")) / 1000.0
    )


def validate_srt_content(
    content: str,
    *,
    duration_seconds: float,
    max_line_chars: int = 42,
    max_lines: int = 2,
) -> dict[str, Any]:
    """Validate SRT cue syntax, monotonic timing, video bounds, and text fit.

    Character/line limits are conservative proxies for likely overflow, not a
    pixel-level safe-area measurement. Human review is still required.
    """
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError, OverflowError):
        duration = float("nan")
    if not math.isfinite(duration) or duration <= 0:
        return {
            "passed": False,
            "status": "blocked",
            "cue_count": 0,
            "errors": [{"code": "invalid_video_duration", "message": "video duration is invalid"}],
            "warnings": [],
            "human_visibility_review_required": True,
        }
    blocks = [
        block.strip()
        for block in re.split(r"\r?\n\s*\r?\n", str(content or "").strip())
        if block.strip()
    ]
    if not blocks:
        errors.append({"code": "no_caption_cues", "message": "caption file contains no cues"})
    last_start = -1.0
    last_end = -1.0
    parsed_count = 0
    for block_index, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        cue_id = lines[0] if lines else str(block_index)
        if len(lines) < 3:
            errors.append({
                "code": "malformed_caption_cue",
                "cue": cue_id,
                "timestamp_seconds": None,
                "message": "cue must contain an index, timing line, and text",
            })
            continue
        timing = re.fullmatch(r"\s*(\d{2,}:[0-5]\d:[0-5]\d,\d{3})\s*-->\s*(\d{2,}:[0-5]\d:[0-5]\d,\d{3})\s*", lines[1])
        if not timing:
            errors.append({
                "code": "malformed_caption_timing",
                "cue": cue_id,
                "timestamp_seconds": None,
                "message": "timing must use HH:MM:SS,mmm --> HH:MM:SS,mmm",
            })
            continue
        start = _srt_seconds(timing.group(1))
        end = _srt_seconds(timing.group(2))
        if start is None or end is None:
            errors.append({
                "code": "malformed_caption_timing",
                "cue": cue_id,
                "timestamp_seconds": None,
                "message": "caption timestamps could not be parsed",
            })
            continue
        parsed_count += 1
        text_lines = lines[2:]
        if not any(line.strip() for line in text_lines):
            errors.append({
                "code": "empty_caption_text",
                "cue": cue_id,
                "timestamp_seconds": start,
                "message": "caption text is empty",
            })
        if end <= start:
            errors.append({
                "code": "non_positive_caption_duration",
                "cue": cue_id,
                "timestamp_seconds": start,
                "message": f"caption ends at {end:.3f}s, not after its start at {start:.3f}s",
            })
        if start < last_start:
            errors.append({
                "code": "non_monotonic_caption_timing",
                "cue": cue_id,
                "timestamp_seconds": start,
                "message": "caption cue starts before the previous cue",
            })
        if start < last_end - 0.02:
            warnings.append({
                "code": "overlapping_caption_cues",
                "cue": cue_id,
                "timestamp_seconds": start,
                "message": f"caption overlaps the previous cue near {start:.3f}s",
            })
        if start > duration + 0.02:
            errors.append({
                "code": "caption_starts_after_video",
                "cue": cue_id,
                "timestamp_seconds": start,
                "message": f"caption starts at {start:.3f}s, after video duration {duration:.3f}s",
            })
        if end > duration + 0.05:
            errors.append({
                "code": "caption_exceeds_video",
                "cue": cue_id,
                "timestamp_seconds": duration,
                "message": f"caption ends at {end:.3f}s, beyond video duration {duration:.3f}s",
            })
        if len(text_lines) > max_lines:
            errors.append({
                "code": "caption_line_overflow",
                "cue": cue_id,
                "timestamp_seconds": start,
                "message": f"caption has {len(text_lines)} lines; maximum is {max_lines}",
            })
        for line_number, text in enumerate(text_lines, start=1):
            if len(text) > max_line_chars:
                errors.append({
                    "code": "caption_text_overflow",
                    "cue": cue_id,
                    "timestamp_seconds": start,
                    "line": line_number,
                    "message": (
                        f"caption line has {len(text)} characters; "
                        f"configured limit is {max_line_chars}"
                    ),
                })
        if start < 0:
            errors.append({
                "code": "negative_caption_time",
                "cue": cue_id,
                "timestamp_seconds": start,
                "message": "caption start time is negative",
            })
        last_start = start
        last_end = max(last_end, end)
    if parsed_count != len(blocks) and blocks and not any(
        item.get("code") == "no_caption_cues" for item in errors
    ):
        warnings.append({
            "code": "some_caption_cues_unparsed",
            "timestamp_seconds": None,
            "message": f"parsed {parsed_count} of {len(blocks)} caption blocks",
        })
    return {
        "passed": not errors,
        "status": "passed" if not errors else "blocked",
        "cue_count": parsed_count,
        "errors": errors,
        "warnings": warnings,
        "human_visibility_review_required": True,
        "safe_area_check": "not_certified_from_SRT; review rendered frames at phone size",
    }


def validate_srt_file(
    path: str | Path,
    *,
    duration_seconds: float,
    max_line_chars: int = 42,
    max_lines: int = 2,
) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file() or target.is_symlink():
        return {
            "passed": False,
            "status": "blocked",
            "cue_count": 0,
            "errors": [{"code": "caption_file_missing", "message": "caption file is missing or not a regular file"}],
            "warnings": [],
            "human_visibility_review_required": True,
        }
    try:
        content = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return {
            "passed": False,
            "status": "blocked",
            "cue_count": 0,
            "errors": [{"code": "caption_file_unreadable", "message": f"could not read caption file: {type(exc).__name__}"}],
            "warnings": [],
            "human_visibility_review_required": True,
        }
    return validate_srt_content(
        content,
        duration_seconds=duration_seconds,
        max_line_chars=max_line_chars,
        max_lines=max_lines,
    )


def audio_signal_report(
    video_path: str | Path,
    *,
    is_gaming: bool = False,
) -> dict[str, Any]:
    """Measure integrated loudness/true peak and report long silent gaps.

    Audio analysis failure is a blocking result; otherwise the report includes
    timestamped silence candidates. It cannot isolate narration from game audio.
    """
    try:
        from .quality_control_v3 import check_audio_levels

        measured = check_audio_levels(str(video_path))
    except Exception as exc:
        return {
            "passed": False,
            "status": "blocked",
            "metrics": {},
            "errors": [{
                "code": "audio_analysis_failed",
                "timestamp_seconds": None,
                "message": f"audio signal analysis failed: {type(exc).__name__}: {exc}",
            }],
            "warnings": [],
            "voice_music_balance": "not_separable_by_integrated_loudness_check",
        }
    if not isinstance(measured, Mapping) or measured.get("error"):
        return {
            "passed": False,
            "status": "blocked",
            "metrics": {},
            "errors": [{
                "code": "audio_analysis_unavailable",
                "timestamp_seconds": None,
                "message": str((measured or {}).get("error") if isinstance(measured, Mapping) else "invalid audio report"),
            }],
            "warnings": [],
            "voice_music_balance": "not_separable_by_integrated_loudness_check",
        }

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    metrics: dict[str, float | None] = {}
    for output_name, source_name in (("integrated_lufs", "integrated_lufs"), ("true_peak_dbtp", "true_peak"), ("loudness_range_lu", "loudness_range")):
        raw = measured.get(source_name)
        try:
            number = float(raw)
            if not math.isfinite(number):
                number = None
        except (TypeError, ValueError, OverflowError):
            number = None
        metrics[output_name] = number

    integrated = metrics["integrated_lufs"]
    true_peak = metrics["true_peak_dbtp"]
    loudness_range = metrics["loudness_range_lu"]
    if integrated is None:
        errors.append({
            "code": "integrated_loudness_unmeasurable",
            "timestamp_seconds": None,
            "message": "integrated loudness could not be measured (silent or invalid audio is possible)",
        })
    elif integrated > -6.0 or integrated < -32.0:
        errors.append({
            "code": "integrated_loudness_out_of_bounds",
            "timestamp_seconds": None,
            "message": f"integrated loudness {integrated:.1f} LUFS is outside the broad QC range -32 to -6 LUFS",
        })
    elif integrated > -10.0 or integrated < -24.0:
        warnings.append({
            "code": "integrated_loudness_review",
            "timestamp_seconds": None,
            "message": f"integrated loudness is {integrated:.1f} LUFS; listen for clipping or low perceived volume",
        })
    if true_peak is None:
        errors.append({
            "code": "true_peak_unmeasurable",
            "timestamp_seconds": None,
            "message": "true peak could not be measured",
        })
    elif true_peak > -0.1:
        errors.append({
            "code": "true_peak_clipping_risk",
            "timestamp_seconds": None,
            "message": f"true peak {true_peak:.2f} dBTP leaves too little headroom; check for clipping",
        })
    elif true_peak > -1.0:
        warnings.append({
            "code": "true_peak_headroom_review",
            "timestamp_seconds": None,
            "message": f"true peak is {true_peak:.2f} dBTP; recommended delivery headroom is at least 1 dB",
        })
    if loudness_range is not None and loudness_range > 20.0:
        warnings.append({
            "code": "wide_loudness_range",
            "timestamp_seconds": None,
            "message": f"loudness range is {loudness_range:.1f} LU; listen for inconsistent level changes",
        })

    for item in measured.get("silence_segments") or []:
        if not isinstance(item, Mapping):
            continue
        try:
            start = float(item.get("start"))
            end_value = item.get("end")
            end = float(end_value) if end_value is not None else None
            gap = float(item.get("duration")) if item.get("duration") is not None else (end - start if end is not None else 0.0)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(start) and gap >= (5.0 if is_gaming else 8.0):
            warning = {
                "code": "long_audio_silence",
                "timestamp_seconds": start,
                "message": f"{gap:.2f}s audio-silence candidate begins at {start:.2f}s",
            }
            if end is not None:
                warning["end_seconds"] = end
            if is_gaming and gap >= 12.0:
                errors.append({**warning, "code": "unexpected_long_gaming_silence"})
            else:
                warnings.append(warning)

    return {
        "passed": not errors,
        "status": "passed" if not errors else "blocked",
        "metrics": metrics,
        "silence_candidates": list(measured.get("silence_segments") or []),
        "errors": errors,
        "warnings": warnings,
        "voice_music_balance": "not_separable_by_integrated_loudness_check",
        "human_listening_review_required": True,
        "target_guidance": {
            "broad_integrated_loudness_range_lufs": [-32, -6],
            "preferred_true_peak_ceiling_dbtp": -1.0,
        },
    }


__all__ = ["audio_signal_report", "validate_srt_content", "validate_srt_file"]
