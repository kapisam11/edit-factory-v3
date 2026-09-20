"""Post-render semantic/editorial QC backed by the scene analyzer."""
from __future__ import annotations

import re
from typing import Any

from .scene_intelligence import analyze_video
from .v3_quality import probe_media


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value).lower()))


def _similarity(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def analyze_render_semantics(path: str) -> dict[str, Any]:
    info = probe_media(path)
    sample_seconds = max(1.0, min(2.5, float(info["duration"]) / 12.0))
    scenes = analyze_video(path, sample_seconds=sample_seconds, enable_ocr=False)
    dead_moments: list[dict[str, Any]] = []
    repeated_pairs: list[dict[str, Any]] = []
    low_motion_count = 0

    for scene in scenes:
        duration = max(0.0, float(scene.end) - float(scene.start))
        if scene.motion_score < 0.12 and scene.audio_energy < 0.10:
            low_motion_count += 1
            if duration >= 1.5:
                dead_moments.append({
                    "start": round(scene.start, 3),
                    "end": round(scene.end, 3),
                    "duration": round(duration, 3),
                })

    for previous, current in zip(scenes, scenes[1:]):
        similarity = _similarity(previous.searchable_text, current.searchable_text)
        if similarity >= 0.85 and abs(previous.motion_score - current.motion_score) < 0.10:
            repeated_pairs.append({
                "first": previous.id,
                "second": current.id,
                "similarity": round(similarity, 3),
            })

    low_motion_ratio = low_motion_count / max(1, len(scenes))
    slideshow_like = len(scenes) >= 4 and low_motion_ratio >= 0.70
    errors = []
    warnings = []
    if dead_moments:
        errors.append("long low-motion/low-audio gaps detected")
    if slideshow_like:
        errors.append("render resembles a low-motion slideshow")
    if repeated_pairs:
        warnings.append("adjacent scenes have highly similar semantic descriptors")

    return {
        "ok": not errors,
        "scene_count": len(scenes),
        "sample_seconds": sample_seconds,
        "dead_moments": dead_moments,
        "repeated_pairs": repeated_pairs,
        "low_motion_ratio": round(low_motion_ratio, 3),
        "slideshow_like": slideshow_like,
        "errors": errors,
        "warnings": warnings,
        "mode": "scene_intelligence",
    }


__all__ = ["analyze_render_semantics"]
