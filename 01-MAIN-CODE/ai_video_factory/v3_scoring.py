"""Single source of truth for V3 heuristic scoring.

These weights are engineering heuristics, not learned or calibrated audience predictions.
"""
from __future__ import annotations

# Named coefficients make tuning and review explicit.
HOOK_WEIGHT_RETENTION = 0.35
PACE_WEIGHT_RETENTION = 0.30
QUALITY_WEIGHT_RETENTION = 0.20
EMOTION_WEIGHT_RETENTION = 0.15
QUALITY_WEIGHT_COMPLETION = 0.45
PACE_WEIGHT_COMPLETION = 0.30
HOOK_WEIGHT_COMPLETION = 0.25
HOOK_WEIGHT_REWATCH = 0.40
EMOTION_WEIGHT_REWATCH = 0.35
QUALITY_WEIGHT_REWATCH = 0.25
EMOTION_WEIGHT_SHAREABILITY = 0.50
QUALITY_WEIGHT_SHAREABILITY = 0.30
HOOK_WEIGHT_SHAREABILITY = 0.20

def heuristic_metrics(*, hook: float, pace: float, quality: float, emotion: float) -> dict[str, float]:
    """Return deterministic 0..100 heuristic scores; never probabilities."""
    values = {
        "retention_score": 100 * (HOOK_WEIGHT_RETENTION * hook + PACE_WEIGHT_RETENTION * pace + QUALITY_WEIGHT_RETENTION * quality + EMOTION_WEIGHT_RETENTION * emotion),
        "completion_score": 100 * (QUALITY_WEIGHT_COMPLETION * quality + PACE_WEIGHT_COMPLETION * pace + HOOK_WEIGHT_COMPLETION * hook),
        "rewatch_score": 100 * (HOOK_WEIGHT_REWATCH * hook + EMOTION_WEIGHT_REWATCH * emotion + QUALITY_WEIGHT_REWATCH * quality),
        "shareability_score": 100 * (EMOTION_WEIGHT_SHAREABILITY * emotion + QUALITY_WEIGHT_SHAREABILITY * quality + HOOK_WEIGHT_SHAREABILITY * hook),
    }
    return {name: round(max(0.0, min(100.0, value)), 1) for name, value in values.items()}
