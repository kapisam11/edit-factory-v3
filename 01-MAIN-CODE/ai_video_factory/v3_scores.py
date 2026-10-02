"""Explicit separation between technical, creative, and performance scores."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


def _bounded(value: float) -> float:
    number = float(value)
    if number != number:
        raise ValueError("score cannot be NaN")
    return round(max(0.0, min(100.0, number)), 1)


@dataclass(frozen=True)
class V3ScoreBundle:
    technical_validity: float
    creative_quality: float
    performance_heuristic: float
    performance_method: str = "weighted heuristic index"
    performance_calibration: str = "uncalibrated"
    technical_scope: str = "blueprint"
    creative_scope: str = "editorial heuristic"

    def __post_init__(self) -> None:
        object.__setattr__(self, "technical_validity", _bounded(self.technical_validity))
        object.__setattr__(self, "creative_quality", _bounded(self.creative_quality))
        object.__setattr__(self, "performance_heuristic", _bounded(self.performance_heuristic))
        if not self.performance_method.strip():
            raise ValueError("performance_method is required")
        if not self.performance_calibration.strip():
            raise ValueError("performance_calibration is required")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_blueprint(
        cls,
        *,
        technical_validity: float,
        creative_quality: float,
        metrics: Mapping[str, float],
    ) -> "V3ScoreBundle":
        values = [float(value) for key, value in metrics.items() if key.endswith("_score")]
        performance = sum(values) / len(values) if values else 0.0
        return cls(
            technical_validity=technical_validity,
            creative_quality=creative_quality,
            performance_heuristic=performance,
        )

    def final_with_render(self, *, technical_validity: float) -> "V3ScoreBundle":
        return V3ScoreBundle(
            technical_validity=technical_validity,
            creative_quality=self.creative_quality,
            performance_heuristic=self.performance_heuristic,
            performance_method=self.performance_method,
            performance_calibration=self.performance_calibration,
            technical_scope="rendered artifact + contract checks",
            creative_scope=self.creative_scope,
        )


__all__ = ["V3ScoreBundle"]
