"""Small, deterministic learning layer for choosing future edit settings.

The recommender is intentionally model-free: it can start with a handful of
experiment records and improve without requiring scikit-learn.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class Recommendation:
    settings: Dict[str, Any]
    confidence: float
    evidence_count: int
    reason: str
    learning: Dict[str, Any] = field(default_factory=dict)


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
    model_path: Optional[str] = None,
) -> Recommendation:
    records = [record for record in experiments if isinstance(record, dict)]
    from .video_learning import recommend_learned_profile, train_preference_model, training_summary

    trained_model = train_preference_model(records, model_path=model_path)
    learning_info = training_summary(trained_model)
    if not records:
        return Recommendation(
            settings=dict(defaults or {}),
            confidence=0.05,
            evidence_count=0,
            reason="Cold start: no experiment history is available.",
            learning=learning_info,
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
    learned_profile = recommend_learned_profile(context, records, trained_model)
    reason = f"Selected settings from {len(ranked)} nearest experiments; best evidence score={_metric_score(best):.3f}."
    if learned_profile:
        learned_settings = learned_profile.get("settings")
        if isinstance(learned_settings, dict):
            # The trained local model ranks only configurations that were actually
            # tried. That keeps recommendations inside the channel's evidence base.
            for key, value in learned_settings.items():
                if key in NUMERIC_FEATURES or key in ("caption_style", "voice", "music_style", "platform", "content_type", "edit_type"):
                    settings[key] = value
        learning_info.update({
            "predicted_reward": learned_profile.get("predicted_reward"),
            "selected_observed_reward": learned_profile.get("observed_reward"),
            "candidate_count": learned_profile.get("candidate_count"),
        })
        reason += (
            f" Local preference model ranked {learned_profile.get('candidate_count')} tested profiles "
            f"from {learned_profile.get('sample_count')} measured/rated videos."
        )
        confidence = max(confidence, min(0.90, 0.35 + 0.04 * int(learned_profile.get("sample_count") or 0)))
    elif trained_model.get("status") == "warming_up":
        reason += " Local preference model is collecting enough real feedback to retrain."

    return Recommendation(
        settings=settings,
        confidence=round(confidence, 3),
        evidence_count=len(ranked),
        reason=reason,
        learning=learning_info,
    )
