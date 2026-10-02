"""Small deterministic helpers for analyzing V3 stage timing evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class StageTiming:
    name: str
    milliseconds: float
    share: float


@dataclass(frozen=True)
class PerformanceReport:
    total_ms: float
    stages: tuple[StageTiming, ...]
    bottleneck: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "total_ms": self.total_ms,
            "bottleneck": self.bottleneck,
            "stages": [
                {"name": item.name, "milliseconds": item.milliseconds, "share": item.share}
                for item in self.stages
            ],
        }


def analyze_stage_timings(timings: Mapping[str, float] | Sequence[tuple[str, float]]) -> PerformanceReport:
    items = [(str(name), float(value)) for name, value in dict(timings).items()] if isinstance(timings, Mapping) else [
        (str(name), float(value)) for name, value in timings
    ]
    if any(value < 0 for _, value in items):
        raise ValueError("stage timings cannot be negative")
    total = sum(value for _, value in items)
    ordered = sorted(items, key=lambda item: item[1], reverse=True)
    stages = tuple(
        StageTiming(
            name=name,
            milliseconds=round(value, 3),
            share=round(value / total, 4) if total else 0.0,
        )
        for name, value in ordered
    )
    return PerformanceReport(
        total_ms=round(total, 3),
        stages=stages,
        bottleneck=stages[0].name if stages else None,
    )


__all__ = ["PerformanceReport", "StageTiming", "analyze_stage_timings"]
