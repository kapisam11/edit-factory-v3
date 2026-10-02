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
        (float(item.get("time", -1)), str(item.get("kind", "")).strip().lower(), str(item.get("instruction", "")).strip())
        for item in events
        if isinstance(item, Mapping)
    )
    checks: dict[str, bool] = {}
    warnings: list[str] = []
    if not values:
        return RetentionEditorialReport(False, 0.0, {"has_events": False}, ("retention map is empty",), None, None, 0.0)

    times = [value[0] for value in values]
    kinds = [value[1] for value in values]
    gaps = [b - a for a, b in zip(times, times[1:])]
    min_gap = min(gaps) if gaps else duration
    max_gap = max(gaps) if gaps else duration
    counts = Counter(kinds)
    diversity = len(counts) / max(1, len(kinds))

    checks["has_events"] = bool(events)
    checks["starts_early"] = times[0] <= 0.35
    checks["positive_gaps"] = all(gap > 0 for gap in gaps)
    checks["not_overcrowded"] = min_gap >= max(0.35, float(target_interval) * 0.20)
    checks["no_long_gaps"] = max_gap <= float(target_interval) + 0.75
    checks["effect_diversity"] = diversity >= (0.30 if len(kinds) >= 6 else 0.20)
    checks["no_three_same_in_row"] = all(
        not (a == b == c) for a, b, c in zip(kinds, kinds[1:], kinds[2:])
    )
    checks["specific_instructions"] = all(len(instruction.split()) >= 4 for _, _, instruction in values)

    if clip_boundaries:
        boundary_set = tuple(float(item) for item in clip_boundaries)
        near_boundary = sum(
            any(abs(timestamp - boundary) <= 0.12 for boundary in boundary_set)
            for timestamp in times
        )
        checks["uses_edit_boundaries"] = near_boundary >= max(1, len(times) // 5)

    for name, ok in checks.items():
        if not ok:
            warnings.append(name.replace("_", " "))

    weight = {
        "has_events": 0.12,
        "starts_early": 0.10,
        "positive_gaps": 0.18,
        "not_overcrowded": 0.15,
        "no_long_gaps": 0.18,
        "effect_diversity": 0.12,
        "no_three_same_in_row": 0.08,
        "specific_instructions": 0.07,
        "uses_edit_boundaries": 0.08,
    }
    active_weights = [weight[key] for key in checks]
    score = 100.0 * sum(weight[key] for key, ok in checks.items() if ok) / max(0.01, sum(active_weights))
    passed = all(
        checks.get(key, True)
        for key in ("has_events", "positive_gaps", "not_overcrowded", "no_long_gaps", "specific_instructions")
    ) and score >= 80.0
    return RetentionEditorialReport(
        passed=passed,
        score=round(score, 1),
        checks=checks,
        warnings=tuple(warnings),
        max_gap=round(max_gap, 3),
        min_gap=round(min_gap, 3),
        diversity=round(diversity, 3),
    )


__all__ = ["RetentionEditorialReport", "evaluate_retention_editorial_fit"]
