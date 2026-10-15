"""Deterministic caption-plan QC before a V3 package can be released.

This checks the supplied overlay contract and renderer geometry when geometry is
available. Pixel-level readability still requires inspection of the encoded video.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping


def validate_caption_geometry(
    position: Mapping[str, Any],
    *,
    frame_width: int,
    frame_height: int,
    safe_bottom: int = 0,
) -> list[str]:
    """Reject explicitly positioned captions that leave the platform-safe frame."""
    errors: list[str] = []
    if frame_width <= 0 or frame_height <= 0:
        return ["caption frame dimensions must be positive"]
    if safe_bottom < 0 or safe_bottom >= frame_height:
        return ["caption safe_bottom is outside the frame"]
    try:
        x = float(position["x"])
        y = float(position["y"])
        width = float(position["width"])
        height = float(position["height"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return ["caption position must include numeric x, y, width, and height"]
    if not all(math.isfinite(value) for value in (x, y, width, height)):
        return ["caption position values must be finite"]
    if width <= 0 or height <= 0:
        errors.append("caption width and height must be positive")
    if x < 0 or y < 0:
        errors.append("caption starts outside the top/left frame boundary")
    if x + width > frame_width:
        errors.append("caption extends beyond the right frame boundary")
    if y + height > frame_height - safe_bottom:
        errors.append("caption extends into the platform-reserved bottom safe area")
    return errors


def validate_caption_plan(
    blueprint: Mapping[str, Any],
    *,
    platform_profile: Mapping[str, Any],
    target_seconds: float,
    max_words: int = 8,
    max_characters: int = 42,
) -> dict[str, Any]:
    """Check planned overlay text and timing against the target profile."""
    errors: list[str] = []
    warnings = [
        "This is blueprint-level QC. Check the encoded video on a phone-sized display; "
        "automatic metadata checks cannot prove actual caption readability."
    ]
    try:
        target = float(target_seconds)
    except (TypeError, ValueError, OverflowError):
        target = float("nan")
    if not math.isfinite(target) or target <= 0:
        errors.append("caption target duration must be finite and positive")

    try:
        frame_width = int(platform_profile.get("width") or 0)
        frame_height = int(platform_profile.get("height") or 0)
        safe_bottom = int(platform_profile.get("safe_bottom") or 0)
    except (TypeError, ValueError, OverflowError):
        frame_width = frame_height = 0
        safe_bottom = -1
    if frame_width <= 0 or frame_height <= 0:
        errors.append("caption platform profile has invalid frame dimensions")
    if safe_bottom < 0 or (frame_height > 0 and safe_bottom >= frame_height):
        errors.append("caption platform profile has invalid bottom safe margin")

    raw_clips = blueprint.get("clip_plan")
    clips = raw_clips if isinstance(raw_clips, list) else []
    if not clips:
        errors.append("caption plan has no clip beats")

    overlay_count = 0
    previous_overlay = ""
    for index, raw in enumerate(clips, start=1):
        if not isinstance(raw, Mapping):
            errors.append(f"caption beat {index} must be an object")
            continue
        try:
            start = float(raw.get("start"))
            end = float(raw.get("end"))
        except (TypeError, ValueError, OverflowError):
            errors.append(f"caption beat {index} has invalid timing")
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            errors.append(f"caption beat {index} has invalid timing boundaries")
            continue
        if math.isfinite(target) and (start > target + 0.01 or end > target + 0.08):
            errors.append(f"caption beat {index} extends beyond the target duration")
        text = str(raw.get("text_overlay") or "")
        if any(ord(char) < 32 and char not in "\n\t\r" for char in text):
            errors.append(f"caption beat {index} contains control characters")
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            continue
        overlay_count += 1
        words = normalized.split()
        if len(words) > max_words:
            errors.append(
                f"caption beat {index} contains {len(words)} words; maximum is {max_words}"
            )
        if len(normalized) > max_characters:
            errors.append(
                f"caption beat {index} is {len(normalized)} characters; maximum is {max_characters}"
            )
        if end - start < 0.5:
            errors.append(f"caption beat {index} remains visible for less than 0.5 seconds")
        if normalized.casefold() == previous_overlay:
            warnings.append(
                f"caption beat {index} repeats the previous overlay; check for unnecessary repetition"
            )
        previous_overlay = normalized.casefold()

        position = raw.get("position")
        if isinstance(position, Mapping):
            errors.extend(
                f"caption beat {index}: {error}"
                for error in validate_caption_geometry(
                    position,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    safe_bottom=safe_bottom,
                )
            )

    return {
        "schema_version": 1,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "frame_profile_valid": frame_width > 0 and frame_height > 0 and 0 <= safe_bottom < frame_height,
            "clip_timing_valid": not any("timing" in error or "duration" in error for error in errors),
            "overlay_text_valid": not any("caption beat" in error for error in errors),
            "overlay_count": overlay_count,
            "max_words": max_words,
            "max_characters": max_characters,
        },
        "caption_visibility": {
            "status": "manual_review_required",
            "automatic_pixel_ocr": "not_claimed",
        },
    }


__all__ = ["validate_caption_geometry", "validate_caption_plan"]
