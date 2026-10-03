"""Multi-signal visual verification primitives for rendered retention effects."""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any, Mapping


@dataclass(frozen=True)
class VisualVerification:
    pixel_delta: float
    histogram_delta: float
    ssim_like: float
    optical_flow: float | None
    scene_change: bool
    passed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pixel_delta": round(self.pixel_delta, 3),
            "histogram_delta": round(self.histogram_delta, 3),
            "ssim_like": round(self.ssim_like, 4),
            "optical_flow": None if self.optical_flow is None else round(self.optical_flow, 3),
            "scene_change": self.scene_change,
            "passed": self.passed,
            "reason": self.reason,
        }


def _histogram(frame: bytes, bins: int = 16) -> list[float]:
    if not frame:
        return [0.0] * bins
    counts = [0] * bins
    for value in frame:
        counts[min(bins - 1, value * bins // 256)] += 1
    total = float(len(frame))
    return [count / total for count in counts]


def _histogram_delta(first: bytes, second: bytes) -> float:
    a, b = _histogram(first), _histogram(second)
    return sum(abs(x - y) for x, y in zip(a, b)) / 2.0


def _ssim_like(first: bytes, second: bytes) -> float:
    if not first or len(first) != len(second):
        return 0.0
    n = len(first)
    mean_a = sum(first) / n
    mean_b = sum(second) / n
    var_a = sum((x - mean_a) ** 2 for x in first) / n
    var_b = sum((x - mean_b) ** 2 for x in second) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(first, second)) / n
    c1 = 6.5025
    c2 = 58.5225
    numerator = (2 * mean_a * mean_b + c1) * (2 * cov + c2)
    denominator = (mean_a * mean_a + mean_b * mean_b + c1) * (var_a + var_b + c2)
    return max(0.0, min(1.0, numerator / denominator if denominator else 0.0))


def _pixel_delta(first: bytes, second: bytes) -> float:
    if not first or len(first) != len(second):
        return 0.0
    return sum(abs(a - b) for a, b in zip(first, second)) / len(first)


def verify_visual_effect(
    baseline: bytes,
    rendered: bytes,
    *,
    effect_kind: str,
    expected_change: float = 2.5,
    optical_flow: float | None = None,
) -> VisualVerification:
    pixel = _pixel_delta(baseline, rendered)
    histogram = _histogram_delta(baseline, rendered)
    ssim = _ssim_like(baseline, rendered)
    scene_change = histogram >= 0.35 and ssim <= 0.70
    kind = str(effect_kind).strip().lower()
    if kind in {"zoom", "motion", "angle"}:
        passed = pixel >= expected_change and ssim < 0.995
        reason = "spatial-change signal detected" if passed else "insufficient spatial-change evidence"
    elif kind in {"text", "caption"}:
        passed = pixel >= expected_change
        reason = "caption render produced measurable change" if passed else "caption change not measurable"
    elif kind in {"clip", "cut", "beat drop", "transition"}:
        passed = scene_change or pixel >= expected_change
        reason = "scene/transition signal detected" if passed else "transition signal not measurable"
    else:
        passed = False
        reason = "unsupported effect kind"
    return VisualVerification(pixel, histogram, ssim, optical_flow, scene_change, passed, reason)


__all__ = ["VisualVerification", "verify_visual_effect"]
