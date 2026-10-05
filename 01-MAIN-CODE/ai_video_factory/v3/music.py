"""Music planning module."""
from __future__ import annotations

from typing import Any, Sequence


def analyze_music(core: Any, config: Any, clips: Sequence[Any]) -> Any:
    from ..v3_engine import MusicPlan
    beat_seconds = round(60.0 / config.bpm, 4)
    payoff_start = (
        clips[-2].start if len(clips) >= 2 else config.target_seconds * 0.7
    )
    drop = min(config.target_seconds * 0.72, max(2.0, payoff_start))
    sync: list[float] = []
    time_value = 0.0
    while time_value < config.target_seconds - 1e-6:
        sync.append(round(time_value, 3))
        time_value += beat_seconds * 2
    sync.append(round(config.target_seconds, 3))
    return MusicPlan(
        config.bpm,
        "high" if core.target_emotion in {"dramatic", "inspiring", "funny"} else "medium",
        core.target_emotion,
        beat_seconds,
        round(drop, 3),
        sync,
    )


__all__ = ["analyze_music"]
