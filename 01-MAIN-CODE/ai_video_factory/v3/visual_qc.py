"""Multi-signal visual verification primitives for rendered retention effects.

The verifier treats pixel change as evidence, not proof. Each semantic effect has
an additional signal chosen for the intended editorial behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_FRAME_WIDTH = 160
_FRAME_HEIGHT = 90
_FRAME_SIZE = _FRAME_WIDTH * _FRAME_HEIGHT


@dataclass(frozen=True)
class VisualVerification:
    pixel_delta: float
    histogram_delta: float
    ssim_like: float
    optical_flow: float | None
    scene_change: bool
    passed: bool
    reason: str
    semantic_signal: float = 0.0
    spatial_scale_signal: float = 0.0
    lower_band_change: float = 0.0
    structural_change: float = 0.0

    @property
    def semantic_signals(self) -> dict[str, bool]:
        """Backward-compatible named semantic gates for diagnostics/tests."""
        return {
            "spatial_scale_change": self.spatial_scale_signal >= 0.05,
            "lower_band_change": self.lower_band_change >= 0.01,
            "structural_change": self.structural_change >= 0.25,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "pixel_delta": round(self.pixel_delta, 3),
            "histogram_delta": round(self.histogram_delta, 3),
            "ssim_like": round(self.ssim_like, 4),
            "optical_flow": None if self.optical_flow is None else round(self.optical_flow, 3),
            "scene_change": self.scene_change,
            "passed": self.passed,
            "reason": self.reason,
            "semantic_signal": round(self.semantic_signal, 3),
            "spatial_scale_signal": round(self.spatial_scale_signal, 3),
            "lower_band_change": round(self.lower_band_change, 3),
            "structural_change": round(self.structural_change, 3),
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


def _edge_energy(frame: bytes) -> float:
    if len(frame) != _FRAME_SIZE:
        return 0.0
    horizontal = sum(
        abs(frame[row * _FRAME_WIDTH + col] - frame[row * _FRAME_WIDTH + col - 1])
        for row in range(_FRAME_HEIGHT)
        for col in range(1, _FRAME_WIDTH)
    )
    vertical = sum(
        abs(frame[row * _FRAME_WIDTH + col] - frame[(row - 1) * _FRAME_WIDTH + col])
        for row in range(1, _FRAME_HEIGHT)
        for col in range(_FRAME_WIDTH)
    )
    return (horizontal + vertical) / max(1.0, float(2 * _FRAME_SIZE))


def _region_delta(first: bytes, second: bytes, *, y0: int, y1: int) -> float:
    if len(first) != _FRAME_SIZE or len(second) != _FRAME_SIZE:
        return 0.0
    start = max(0, min(_FRAME_HEIGHT, y0))
    end = max(start, min(_FRAME_HEIGHT, y1))
    if end <= start:
        return 0.0
    total = 0
    count = 0
    for row in range(start, end):
        base = row * _FRAME_WIDTH
        for col in range(_FRAME_WIDTH):
            total += abs(first[base + col] - second[base + col])
            count += 1
    return total / max(1, count)


def _center_edge_contrast(frame: bytes) -> float:
    if len(frame) != _FRAME_SIZE:
        return 0.0
    cx0, cx1 = _FRAME_WIDTH // 4, (_FRAME_WIDTH * 3) // 4
    cy0, cy1 = _FRAME_HEIGHT // 4, (_FRAME_HEIGHT * 3) // 4
    center = []
    outer = []
    for row in range(_FRAME_HEIGHT):
        for col in range(_FRAME_WIDTH):
            value = frame[row * _FRAME_WIDTH + col]
            if cx0 <= col < cx1 and cy0 <= row < cy1:
                center.append(value)
            else:
                outer.append(value)
    center_mean = sum(center) / max(1, len(center))
    outer_mean = sum(outer) / max(1, len(outer))
    return abs(center_mean - outer_mean) / 255.0


def _spatial_scale_signal(first: bytes, second: bytes) -> float:
    center_change = abs(_center_edge_contrast(first) - _center_edge_contrast(second))
    edge_change = abs(_edge_energy(first) - _edge_energy(second))
    return max(0.0, min(1.0, 0.65 * min(1.0, center_change * 4.0) + 0.35 * min(1.0, edge_change * 6.0)))


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
    spatial_scale = _spatial_scale_signal(baseline, rendered)
    lower_band = _region_delta(
        baseline,
        rendered,
        y0=int(_FRAME_HEIGHT * 0.68),
        y1=_FRAME_HEIGHT,
    ) / 255.0
    structural = max(
        0.0,
        min(
            1.0,
            0.45 * min(1.0, histogram * 2.0)
            + 0.35 * min(1.0, max(0.0, 1.0 - ssim) * 8.0)
            + 0.20 * spatial_scale,
        ),
    )
    scene_change = histogram >= 0.35 and ssim <= 0.70
    kind = str(effect_kind).strip().lower()

    if kind in {"zoom", "motion", "angle"}:
        semantic_signal = max(spatial_scale, min(1.0, optical_flow or 0.0))
        passed = pixel >= expected_change and ssim < 0.995 and semantic_signal >= 0.05
        reason = "spatial-scale/reframe evidence detected" if passed else "insufficient spatial-scale/reframe evidence"
    elif kind in {"text", "caption"}:
        semantic_signal = lower_band
        passed = pixel >= expected_change and lower_band >= 0.01
        reason = "lower-band caption evidence detected" if passed else "caption-specific visual evidence not measurable"
    elif kind in {"clip", "cut"}:
        semantic_signal = structural
        passed = scene_change or (semantic_signal >= 0.35 and pixel >= expected_change)
        reason = "shot-transition evidence detected" if passed else "shot-transition evidence not measurable"
    elif kind in {"beat drop", "transition"}:
        semantic_signal = max(structural, histogram)
        passed = scene_change or (semantic_signal >= 0.25 and pixel >= expected_change)
        reason = "transition evidence detected" if passed else "transition evidence not measurable"
    else:
        semantic_signal = 0.0
        passed = False
        reason = "unsupported effect kind"

    return VisualVerification(
        pixel,
        histogram,
        ssim,
        optical_flow,
        scene_change,
        passed,
        reason,
        semantic_signal,
        spatial_scale,
        lower_band,
        structural,
    )


__all__ = ["VisualVerification", "verify_visual_effect"]
