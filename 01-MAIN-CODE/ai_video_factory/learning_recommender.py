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


EDIT_NUMERIC_FEATURES = ("cuts_per_minute", "avg_shot_duration", "hook_duration", "music_energy")
NUMERIC_FEATURES = (*EDIT_NUMERIC_FEATURES, "duration_seconds")
CATEGORICAL_CONTEXT_FEATURES = (
    "platform", "content_type", "format", "game", "hook_type",
    "commentary_style", "caption_style", "voice", "music_style", "edit_type",
)


def _distance(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    distance = 0.0
    for key in NUMERIC_FEATURES:
        if key not in a or key not in b:
            continue
        try:
            av = float(a[key])
            bv = float(b[key])
        except (TypeError, ValueError):
            continue
        scale = max(1.0, abs(av), abs(bv))
        distance += ((av - bv) / scale) ** 2
    for key in CATEGORICAL_CONTEXT_FEATURES:
        if key in a and key in b and a.get(key) and b.get(key) and str(a.get(key)).casefold() != str(b.get(key)).casefold():
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


def build_recommendation_context(
    summary: Dict[str, Any],
    *,
    platform: str,
    target_seconds: float,
) -> Dict[str, Any]:
    """Build explicit learning context without treating target duration as an outcome."""
    raw_profile = summary.get("gaming_profile")
    profile = raw_profile if isinstance(raw_profile, dict) else {}
    game = str(
        profile.get("game_name")
        or profile.get("game")
        or summary.get("game")
        or summary.get("game_name")
        or ""
    ).strip()
    angle = str(
        profile.get("creator_angle")
        or summary.get("commentary_style")
        or summary.get("creator_angle")
        or ""
    ).strip().lower()
    hook_type = str(summary.get("hook_type") or "").strip().lower()
    if not hook_type:
        hook_type = {
            "clutch": "decisive_action",
            "strategy": "instructional_problem",
            "funny": "reaction_or_reversal",
            "challenge": "challenge",
            "storytelling": "turning_point",
        }.get(angle, "unspecified")

    try:
        duration = float(target_seconds)
    except (TypeError, ValueError, OverflowError):
        duration = 45.0
    if not 1.0 <= duration <= 3600.0:
        duration = 45.0
    format_kind = "short" if duration <= 60.0 else "long"

    context: Dict[str, Any] = {
        "platform": str(platform or "youtube_shorts").strip().lower(),
        "content_type": str(
            summary.get("content_type") or ("gaming" if profile or game else "short_video")
        ).strip().lower(),
        "format": str(summary.get("format") or f"{platform}:{format_kind}").strip().lower(),
        "duration_seconds": duration,
        "cuts_per_minute": summary.get("cuts_per_minute", 12.0),
        "avg_shot_duration": summary.get("avg_shot_duration", 2.5),
        "hook_duration": summary.get("hook_duration", 2.0),
        "music_energy": summary.get("music_energy", 0.5),
        "hook_type": hook_type,
        "commentary_style": angle or str(summary.get("commentary_style") or "unspecified"),
    }
    if game:
        context["game"] = game
    return context


def recommend(
    context: Dict[str, Any],
    experiments: Iterable[Dict[str, Any]],
    *,
    defaults: Optional[Dict[str, Any]] = None,
    neighbors: int = 8,
    model_path: Optional[str] = None,
) -> Recommendation:
    records = [record for record in experiments if isinstance(record, dict)]
    from .video_learning import (
        eligible_examples,
        recommend_learned_profile,
        train_preference_model,
        training_summary,
    )

    # Fit general weights from all genuine outcomes, but don't present them as
    # evidence-backed for a game with too few comparable videos.
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

    requested_game = str(context.get("game") or "").strip()
    game_sample_count = 0
    if requested_game:
        game_records = [
            row for row in eligible_examples(records)
            if str(row.get("game") or "").strip().casefold() == requested_game.casefold()
        ]
        game_sample_count = len(game_records)
        learning_info["context_game"] = requested_game
        learning_info["context_game_sample_count"] = game_sample_count
        if game_sample_count < 3:
            learning_info["contextual_recommendation"] = "insufficient_game_specific_data"
            uncertainty = (
                f"Only {game_sample_count} measured/rated videos for {requested_game}; "
                "using baseline settings rather than claiming a game-specific learned winner."
            )
            learning_info["uncertainty"] = uncertainty
            return Recommendation(
                settings=dict(defaults or {}),
                confidence=0.15,
                evidence_count=game_sample_count,
                reason=uncertainty,
                learning=learning_info,
            )
        # Rank only configurations actually tried in this game.
        records = [dict(row) for row in game_records]

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
            if key in EDIT_NUMERIC_FEATURES or key in ("caption_style", "voice", "music_style"):
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

    if requested_game:
        learning_info["contextual_recommendation"] = "game_specific_candidates"
        if game_sample_count < 6:
            confidence = min(confidence, 0.40)
            learning_info["uncertainty"] = (
                f"Only {game_sample_count} measured/rated videos for {requested_game}; "
                "this is an early experiment, not strong evidence of a winning style."
            )
        elif game_sample_count < 10:
            confidence = min(confidence, 0.65)
            learning_info["uncertainty"] = (
                f"{game_sample_count} measured/rated videos for {requested_game}; "
                "recommendations remain provisional until more comparable results exist."
            )
        else:
            learning_info["uncertainty"] = (
                f"Recommendation is based on {game_sample_count} measured/rated videos for {requested_game}; "
                "continue checking later uploads against the baseline."
            )

    return Recommendation(
        settings=settings,
        confidence=round(confidence, 3),
        evidence_count=len(ranked),
        reason=reason,
        learning=learning_info,
    )
