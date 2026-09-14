"""Small, deterministic learning layer for choosing future edit settings.

The recommender is intentionally model-free: it can start with a handful of
experiment records and improve without requiring scikit-learn.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class Recommendation:
    settings: Dict[str, Any]
    confidence: float
    evidence_count: int
    reason: str


NUMERIC_FEATURES = ("cuts_per_minute", "avg_shot_duration", "hook_duration", "music_energy")


def _distance(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    distance = 0.0
    for key in NUMERIC_FEATURES:
        try:
            av = float(a.get(key, 0.0))
            bv = float(b.get(key, 0.0))
        except (TypeError, ValueError):
            continue
        scale = max(1.0, abs(av), abs(bv))
        distance += ((av - bv) / scale) ** 2
    for key in ("platform", "content_type", "caption_style", "voice"):
        if key in a and key in b and a.get(key) != b.get(key):
            distance += 0.35
    return distance ** 0.5


def _metric_score(record: Dict[str, Any]) -> float:
    for key in ("engagement", "retention", "performance_score", "score"):
        try:
            if key in record:
                return float(record[key])
        except (TypeError, ValueError):
            pass
    return 0.0


def load_experiments(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("experiments", "records", "results"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return []


def recommend(
    context: Dict[str, Any],
    experiments: Iterable[Dict[str, Any]],
    *,
    defaults: Optional[Dict[str, Any]] = None,
    neighbors: int = 8,
) -> Recommendation:
    records = list(experiments)
    if not records:
        return Recommendation(
            settings=dict(defaults or {}),
            confidence=0.05,
            evidence_count=0,
            reason="Cold start: no experiment history is available.",
        )

    ranked = sorted(records, key=lambda record: _distance(context, record))[: max(1, neighbors)]
    ranked = [r for r in ranked if isinstance(r, dict)]
    weighted: Dict[str, float] = {}
    total_weight = 0.0
    for record in ranked:
        distance = _distance(context, record)
        similarity = 1.0 / (0.1 + distance)
        performance = max(0.0, _metric_score(record))
        weight = similarity * (1.0 + performance)
        total_weight += weight
        for key, value in record.items():
            if key in context or key in NUMERIC_FEATURES or key in ("caption_style", "voice", "music_style"):
                if isinstance(value, (int, float)):
                    weighted[key] = weighted.get(key, 0.0) + float(value) * weight

    settings = dict(defaults or {})
    # Prefer the categorical settings of the best-performing nearby record.
    best = max(ranked, key=_metric_score)
    for key in ("caption_style", "voice", "music_style", "platform"):
        if key in best:
            settings[key] = best[key]

    if total_weight:
        for key, value in weighted.items():
            settings[key] = round(value / total_weight, 4)

    confidence = min(0.95, 0.20 + 0.10 * len(ranked) + 0.15 * min(1.0, max(0.0, _metric_score(best))))
    return Recommendation(
        settings=settings,
        confidence=round(confidence, 3),
        evidence_count=len(ranked),
        reason=f"Selected settings from {len(ranked)} nearest experiments; best evidence score={_metric_score(best):.3f}.",
    )
