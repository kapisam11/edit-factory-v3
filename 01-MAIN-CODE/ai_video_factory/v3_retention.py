"""Editorial retention-event analysis separate from technical event existence."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class RetentionEditorialReport:
    passed: bool
    score: float
    checks: Mapping[str, bool]
    warnings: tuple[str, ...]
    max_gap: float | None
    min_gap: float | None
    diversity: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_retention_editorial_fit(
    events: Sequence[Mapping[str, object]],
    *,
    duration: float,
    target_interval: float,
    clip_boundaries: Sequence[float] = (),
) -> RetentionEditorialReport:
    values = sorted(
        (
            float(item.get("time", -1.0)),
            str(item.get("kind", "")).strip().lower(),
            str(item.get("instruction", "")).strip(),
        )
        for item in events
        if isinstance(item, Mapping)
    )
    if not values:
        return RetentionEditorialReport(
            False,
            0.0,
            {"has_events": False},
            ("retention map is empty",),
            None,
            None,
            0.0,
        )

    times = [value[0] for value in values]
    kinds = [value[1] for value in values]
    gaps = [b - a for a, b in zip(times, times[1:])]
    min_gap = min(gaps) if gaps else float(duration)
    max_gap = max(gaps) if gaps else float(duration)
    counts = Counter(kinds)
    diversity = len(counts) / max(1, len(kinds))

    checks: dict[str, bool] = {
        "has_events": bool(events),
        "events_within_duration": all(0.0 <= time < float(duration) for time in times),
        "starts_early": times[0] <= 0.35,
        "positive_gaps": all(gap > 0 for gap in gaps),
        "not_overcrowded": min_gap >= max(0.35, float(target_interval) * 0.20),
        "no_long_gaps": max_gap <= float(target_interval) + 0.75,
        "effect_diversity": diversity >= (0.30 if len(kinds) >= 6 else 0.20),
        "no_three_same_in_row": all(
            not (a == b == c) for a, b, c in zip(kinds, kinds[1:], kinds[2:])
        ),
        "specific_instructions": all(len(instruction.split()) >= 4 for _, _, instruction in values),
    }

    if clip_boundaries:
        boundary_set = tuple(float(item) for item in clip_boundaries)
        near_boundary = sum(
            any(abs(timestamp - boundary) <= 0.12 for boundary in boundary_set)
            for timestamp in times
        )
        checks["uses_edit_boundaries"] = near_boundary >= max(1, len(times) // 5)

    warnings = tuple(
        name.replace("_", " ")
        for name, ok in checks.items()
        if not ok
    )
    weights = {
        "has_events": 0.10,
        "events_within_duration": 0.12,
        "starts_early": 0.08,
        "positive_gaps": 0.17,
        "not_overcrowded": 0.15,
        "no_long_gaps": 0.16,
        "effect_diversity": 0.10,
        "no_three_same_in_row": 0.06,
        "specific_instructions": 0.06,
        "uses_edit_boundaries": 0.10,
    }
    denominator = sum(weights[key] for key in checks)
    score = 100.0 * sum(weights[key] for key, ok in checks.items() if ok) / max(0.01, denominator)
    passed = all(
        checks.get(key, True)
        for key in (
            "has_events",
            "events_within_duration",
            "positive_gaps",
            "not_overcrowded",
            "no_long_gaps",
            "specific_instructions",
        )
    ) and score >= 80.0
    return RetentionEditorialReport(
        passed=passed,
        score=round(score, 1),
        checks=checks,
        warnings=warnings,
        max_gap=round(max_gap, 3),
        min_gap=round(min_gap, 3),
        diversity=round(diversity, 3),
    )


__all__ = ["RetentionEditorialReport", "evaluate_retention_editorial_fit"]
