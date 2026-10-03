"""Multi-signal, effect-specific visual verification primitives."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


FRAME_WIDTH = 160
FRAME_HEIGHT = 90


@dataclass(frozen=True)
class VisualVerification:
    pixel_delta: float
    histogram_delta: float
    ssim_like: float
    optical_flow: float | None
    scene_change: bool
    semantic_signals: Mapping[str, float | bool | None]
    passed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pixel_delta": round(self.pixel_delta, 3),
            "histogram_delta": round(self.histogram_delta, 3),
            "ssim_like": round(self.ssim_like, 4),
            "optical_flow": None if self.optical_flow is None else round(self.optical_flow, 3),
            "scene_change": self.scene_change,
            "semantic_signals": dict(self.semantic_signals),
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


def _region_delta(first: bytes, second: bytes, x0: int, x1: int, y0: int, y1: int) -> float:
    if len(first) != FRAME_WIDTH * FRAME_HEIGHT or len(second) != len(first):
        return 0.0
    values: list[int] = []
    for y in range(max(0, y0), min(FRAME_HEIGHT, y1)):
        start = y * FRAME_WIDTH
        for x in range(max(0, x0), min(FRAME_WIDTH, x1)):
            index = start + x
            values.append(abs(first[index] - second[index]))
    return sum(values) / max(1, len(values))


def _spatial_signals(first: bytes, second: bytes) -> dict[str, float]:
    center = _region_delta(first, second, 40, 120, 20, 70)
    outer = _region_delta(first, second, 0, 160, 0, 90)
    return {
        "center_delta": round(center, 3),
        "outer_delta": round(outer, 3),
        "center_focus_ratio": round(center / max(0.001, outer), 3),
    }


def verify_visual_effect(
    baseline: bytes,
    rendered: bytes,
    *,
    effect_kind: str,
    expected_change: float = 2.5,
    optical_flow: float | None = None,
    semantic_context: Mapping[str, Any] | None = None,
) -> VisualVerification:
    pixel = _pixel_delta(baseline, rendered)
    histogram = _histogram_delta(baseline, rendered)
    ssim = _ssim_like(baseline, rendered)
    scene_change = histogram >= 0.35 and ssim <= 0.70
    kind = str(effect_kind).strip().lower()
    spatial = _spatial_signals(baseline, rendered)
    signals: dict[str, float | bool | None] = dict(spatial)
    context = semantic_context or {}

    if kind == "zoom":
        spatial_ok = spatial["center_focus_ratio"] >= 1.01 and spatial["center_delta"] >= expected_change * 0.5
        passed = pixel >= expected_change and ssim < 0.995 and spatial_ok
        signals["spatial_scale_change"] = spatial_ok
        reason = "zoom spatial-scale evidence detected" if passed else "zoom lacks localized spatial-scale evidence"
    elif kind == "motion":
        flow_ok = optical_flow is not None and optical_flow >= 0.5
        proxy_ok = pixel >= expected_change and histogram < 0.35 and ssim < 0.995
        passed = flow_ok or proxy_ok
        signals["motion_flow"] = optical_flow
        signals["motion_proxy"] = proxy_ok
        reason = "motion evidence detected" if passed else "motion evidence not established"
    elif kind in {"angle", "crop"}:
        reframe_ok = spatial["center_delta"] >= expected_change * 0.5 and ssim < 0.995
        passed = pixel >= expected_change and reframe_ok
        signals["reframe_change"] = reframe_ok
        reason = "reframe evidence detected" if passed else "reframe evidence not established"
    elif kind in {"text", "caption"}:
        expected_text = str(context.get("expected_text") or "").strip()
        ocr_text = str(context.get("ocr_text") or "").strip()
        text_observed = bool(ocr_text) and (
            not expected_text or all(token.lower() in ocr_text.lower() for token in expected_text.split()[:4])
        )
        pixel_localized = max(spatial["center_delta"], _region_delta(baseline, rendered, 0, 160, 60, 90)) >= expected_change
        passed = text_observed or pixel_localized
        signals["ocr_text_observed"] = text_observed
        signals["text_pixel_localized"] = pixel_localized
        reason = "caption/text evidence detected" if passed else "caption/text evidence not measurable"
    elif kind in {"clip", "cut"}:
        passed = scene_change
        signals["required_scene_change"] = True
        reason = "source-shot change detected" if passed else "source-shot change not detected"
    elif kind in {"beat drop", "transition"}:
        transition_signal = scene_change or (histogram >= 0.18 and ssim <= 0.82)
        passed = transition_signal
        signals["transition_signal"] = transition_signal
        reason = "transition evidence detected" if passed else "transition evidence not established"
    else:
        passed = False
        reason = "unsupported effect kind"

    return VisualVerification(
        pixel,
        histogram,
        ssim,
        optical_flow,
        scene_change,
        signals,
        passed,
        reason,
    )


__all__ = ["VisualVerification", "verify_visual_effect"]
