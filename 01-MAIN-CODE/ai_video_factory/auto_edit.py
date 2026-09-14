"""Automated short composer — compatibility re-exports.

This module re-exports the composition pipeline from the split submodules
for backward compatibility. New code should import directly from:
- composer.compose_short_from_video
- segment_engine for segment/beat logic
- effects_engine for filters
- render_engine for ffmpeg operations
"""
from .composer import compose_short_from_video  # noqa: F401

# Legacy re-exports for any tools importing these directly
from .segment_engine import detect_beats, get_segments, snap_to_beat  # noqa: F401
from .effects_engine import build_cinematic_filter  # noqa: F401
from .render_engine import run_ffmpeg, _ensure_dir  # noqa: F401


def _build_cinematic_filter(index: int, label: str, duration: float):
    """Backward-compatible helper used by the legacy auto-edit tests."""
    return build_cinematic_filter(index, label, duration)
