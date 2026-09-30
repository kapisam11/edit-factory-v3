"""Technical media metadata extraction backed by FFprobe.

The extractor returns normalized codec/container/timing/dimension/audio/color metadata
without copying arbitrary tags into the production artifact. Potentially sensitive
location/comment/author tags are excluded unless explicitly requested.
"""
from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping

from .render_engine import run_ffprobe


_SENSITIVE_TAG_KEYS = {
    "location",
    "location-eng",
    "location-iso6709",
    "gps",
    "latitude",
    "longitude",
    "artist",
    "author",
    "comment",
    "description",
    "title",
}

_SAFE_FORMAT_KEYS = {
    "format_name",
    "format_long_name",
    "duration",
    "size",
    "bit_rate",
    "start_time",
    "probe_score",
}


def _safe_tag_map(tags: Mapping[str, Any] | None, *, include_sensitive: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_key, raw_value in (tags or {}).items():
        key = str(raw_key).strip()
        normalized = key.lower().replace("_", "-")
        if not include_sensitive and (
            normalized in _SENSITIVE_TAG_KEYS
            or any(token in normalized for token in ("gps", "location", "latitude", "longitude"))
        ):
            continue
        value = str(raw_value)
        if len(value) > 512:
            value = value[:512]
        result[key] = value
    return result


def _fps(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    try:
        result = float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return None
    return round(result, 6) if math.isfinite(result) else None


def _stream_summary(stream: Mapping[str, Any], *, include_sensitive: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "index": stream.get("index"),
        "codec_type": stream.get("codec_type"),
        "codec_name": stream.get("codec_name"),
        "codec_long_name": stream.get("codec_long_name"),
        "profile": stream.get("profile"),
        "width": stream.get("width"),
        "height": stream.get("height"),
        "pix_fmt": stream.get("pix_fmt"),
        "r_frame_rate": stream.get("r_frame_rate"),
        "avg_frame_rate": stream.get("avg_frame_rate"),
        "fps": _fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate")),
        "sample_aspect_ratio": stream.get("sample_aspect_ratio"),
        "display_aspect_ratio": stream.get("display_aspect_ratio"),
        "color_range": stream.get("color_range"),
        "color_space": stream.get("color_space"),
        "color_transfer": stream.get("color_transfer"),
        "color_primaries": stream.get("color_primaries"),
        "sample_rate": stream.get("sample_rate"),
        "channels": stream.get("channels"),
        "channel_layout": stream.get("channel_layout"),
        "start_time": stream.get("start_time"),
        "duration": stream.get("duration"),
        "bit_rate": stream.get("bit_rate"),
        "tags": _safe_tag_map(stream.get("tags"), include_sensitive=include_sensitive),
    }
    return {key: value for key, value in summary.items() if value not in (None, "", {}, [])}


def extract_media_metadata(path: str | Path, *, include_sensitive: bool = False) -> dict[str, Any]:
    """Probe and normalize technical metadata for one media artifact."""
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)

    result = run_ffprobe(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            "-of",
            "json",
            str(target),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[-2000:]
        raise RuntimeError(f"FFprobe metadata extraction failed: {detail}")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("FFprobe metadata output was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("FFprobe metadata output was not an object")

    raw_format = payload.get("format") or {}
    streams = payload.get("streams") or []
    metadata = {
        "schema_version": 1,
        "path": target.name,
        "format": {
            key: raw_format.get(key)
            for key in _SAFE_FORMAT_KEYS
            if raw_format.get(key) not in (None, "")
        },
        "streams": [
            _stream_summary(stream, include_sensitive=include_sensitive)
            for stream in streams
            if isinstance(stream, Mapping)
        ],
        "chapters": [
            {
                "id": chapter.get("id"),
                "start_time": chapter.get("start_time"),
                "end_time": chapter.get("end_time"),
                "tags": _safe_tag_map(chapter.get("tags"), include_sensitive=include_sensitive),
            }
            for chapter in (payload.get("chapters") or [])
            if isinstance(chapter, Mapping)
        ],
        "tags": _safe_tag_map(
            raw_format.get("tags"),
            include_sensitive=include_sensitive,
        ),
    }
    if include_sensitive:
        metadata["sensitive_metadata_requested"] = True
    return metadata


__all__ = ["extract_media_metadata"]
