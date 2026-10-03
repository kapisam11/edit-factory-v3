"""V3 creative planner facade.

Planning is split into hooks, clips, music, retention and metadata modules.
This module orchestrates those pure planning steps and builds the compatibility
V3Blueprint consumed by the rest of the pipeline.
"""
from __future__ import annotations

from dataclasses import asdict
import re
from typing import Any, Mapping, Sequence

from .audience import parse_audience
from .clips import (
    allocate_durations,
    build_clip_plan,
    compact_overlay_text,
    overlay_for_purpose,
    unique_overlay,
)
from .hooks import generate_hooks
from .music import analyze_music
from .platform_policy import POLICIES
from .retention import build_retention_map
from ..idempotency import stable_hash
from ..v3_scoring import heuristic_metrics


def analyze_core_idea(
    topic: str,
    context: str = "",
    audience: str = "general short-form viewers",
) -> Any:
    from ..v3_engine import CoreIdea
    from ..v3_semantics import combined_scores

    topic = str(topic).strip()
    context = str(context).strip()
    if not topic:
        raise ValueError("topic is required")

    scores = combined_scores(topic, context)
    audience_profile = parse_audience(audience)
    interests = set(audience_profile.interests)
    if "comedy" in interests:
        scores["funny"] += 0.05
    if {"history", "science", "education"} & interests:
        scores["curious"] += 0.05
    if {"gaming", "minecraft", "fortnite"} & interests:
        scores["dramatic"] += 0.02
    if "anime" in interests:
        scores["dramatic"] += 0.02

    emotion = max(scores, key=scores.get) if any(scores.values()) else "curious"
    angles = {
        "trust": (
            f"Why {topic} became impossible to ignore",
            "Loyalty under pressure creates immediate human stakes.",
            "Trust can be earned quickly and lost in one decision.",
            f"The final proof that changes how viewers read {topic}.",
        ),
        "dramatic": (
            f"The decision that changed everything in {topic}",
            "Conflict and consequences create an immediate information gap.",
            "Something valuable can be lost before the viewer understands why.",
            "Reveal the consequence viewers were waiting to understand.",
        ),
        "inspiring": (
            f"How {topic} kept going when quitting was easier",
            "Effort matters when failure feels possible and progress is visible.",
            "The attempt only matters if the outcome is uncertain.",
            "Show the result that makes the struggle worth it.",
        ),
        "nostalgic": (
            f"Why {topic} still feels unforgettable",
            "Recognition and shared memory create instant emotional pull.",
            "The memory only works when the details feel specific.",
            "Return to the moment viewers wanted to remember.",
        ),
        "funny": (
            f"The moment {topic} went completely off the rails",
            "Fast setup plus escalation makes the reversal worth waiting for.",
            "The joke dies when setup overwhelms the punchline.",
            "Deliver the cleanest reaction or reversal last.",
        ),
        "curious": (
            f"The part of {topic} most people miss",
            "A knowledge gap gives the viewer a reason to stay.",
            "The answer must be more valuable than the setup.",
            "Resolve the question with one clear memorable insight.",
        ),
    }
    angle, care, stakes, payoff = angles[emotion]
    return CoreIdea(
        topic,
        care,
        angle,
        emotion,
        f"The viewer needs the final proof behind: {angle}.",
        payoff,
        stakes,
    )


def choose_edit_type(core: Any, requested: str | None = None) -> Any:
    from ..v3_engine import EditType, EDIT_BY_EMOTION

    if requested:
        for item in EditType:
            if item.value.lower() == str(requested).strip().lower():
                return item
        raise ValueError(f"unknown edit type: {requested}")
    return EDIT_BY_EMOTION.get(core.target_emotion, EditType.STORYTELLING)


def heuristic_metrics_for_plan(
    core: Any,
    hooks: Sequence[Any],
    clips: Sequence[Any],
    quality: Any,
) -> dict[str, float]:
    hook = hooks[0].score if hooks else 0.0
    average_clip = clips[-1].end / len(clips) if clips else 0.0
    pace = min(1.0, 2.8 / max(average_clip, 0.1))
    quality_value = quality.score / 100.0
    emotion = (
        0.9
        if core.target_emotion in {"trust", "dramatic", "inspiring", "nostalgic"}
        else 0.82
    )
    return heuristic_metrics(
        hook=hook,
        pace=pace,
        quality=quality_value,
        emotion=emotion,
    )


def _planned_metadata(
    core: Any,
    audience_profile: Any | None = None,
) -> tuple[str, list[str], list[str], str]:
    topic = compact_overlay_text(core.topic, 8)
    angle = compact_overlay_text(core.emotional_angle, 10)
    payoff = compact_overlay_text(core.payoff, 12)
    emotion = compact_overlay_text(core.target_emotion, 3)

    titles = list(
        dict.fromkeys(
            [
                compact_overlay_text(f"{topic}: {angle}", 14),
                compact_overlay_text(f"The moment {topic} changed", 10),
                compact_overlay_text(f"What happened with {topic}", 10),
                compact_overlay_text(
                    f"The part of {topic} most people miss", 12
                ),
                compact_overlay_text(f"Why {topic} still matters", 10),
                compact_overlay_text(f"The turning point in {topic}", 11),
                compact_overlay_text(
                    f"{topic}: the detail that changes the story", 12
                ),
                compact_overlay_text(f"How {topic} changed everything", 10),
            ]
        )
    )
    topic_tags = [
        token for token in re.findall(r"[A-Za-z0-9]{3,}", core.topic.lower())
    ]
    tags = list(dict.fromkeys([*topic_tags, emotion]))
    description = " ".join(
        part
        for part in (
            re.sub(r"\s+", " ", core.emotional_angle).strip(),
            re.sub(r"\s+", " ", core.why_people_care).strip(),
            re.sub(r"\s+", " ", payoff).strip(),
        )
        if part
    )
    thumbnail = (
        f"Use the strongest focal frame from {topic}, keep the subject clear, "
        "leave text-safe space, and use a bold 2-5 word hook tied to the story."
    )
    return thumbnail, titles[:10], tags[:12], description


def create_blueprint(
    topic: str,
    *,
    context: str = "",
    config: Any | None = None,
    edit_type: str | None = None,
    footage_evidence: Mapping[str, Any] | None = None,
) -> Any:
    from ..v3_engine import (
        ClipBeat,
        ClipEvidence,
        V3Blueprint,
        V3Config,
        V3_CAPABILITIES,
        automated_editorial_checks,
        validate_blueprint,
    )
    from .clip_evidence import build_clip_evidence

    cfg = config or V3Config()
    cfg.validate()
    core = analyze_core_idea(topic, context, cfg.audience)
    selected = choose_edit_type(core, edit_type)
    evidence_context = dict(footage_evidence or {})
    evidence_context["audience_label"] = cfg.audience

    hooks = generate_hooks(
        core,
        selected,
        source_evidence=evidence_context,
    )
    clips = build_clip_plan(core, selected, cfg)

    if footage_evidence:
        evidence_rows = build_clip_evidence(
            [asdict(item) for item in clips],
            dict(footage_evidence),
            source_asset=str(
                dict(footage_evidence).get("source_asset", "")
            ),
        )
        by_index = {
            int(row.get("clip_index", 0)): row
            for row in evidence_rows
        }
        enriched: list[Any] = []
        for clip in clips:
            row = by_index.get(clip.index, {})
            enriched.append(
                ClipBeat(
                    clip.index,
                    clip.start,
                    clip.end,
                    clip.purpose,
                    clip.emotion,
                    clip.visual_style,
                    clip.text_overlay,
                    clip.camera_motion,
                    clip.transition,
                    ClipEvidence(
                        source_asset=str(row.get("source_asset", "")),
                        source_start=float(row.get("source_start", 0.0)),
                        source_end=float(row.get("source_end", 0.0)),
                        semantic_tags=tuple(
                            str(x) for x in row.get("semantic_tags", ())
                        ),
                        evidence_score=float(
                            row.get("evidence_score", 0.0)
                        ),
                        evidence_status=str(
                            row.get("status", "unsupported")
                        ),
                    ),
                )
            )
        clips = enriched

    music = analyze_music(core, cfg, clips)
    retention = build_retention_map(cfg, clips, music)
    thumbnail, titles, tags, description = _planned_metadata(
        core,
        parse_audience(cfg.audience),
    )
    quality = automated_editorial_checks(
        core,
        selected,
        hooks,
        clips,
        retention,
        cfg,
    )
    metrics = heuristic_metrics_for_plan(core, hooks, clips, quality)
    clip_purposes = {
        round(clip.start, 3): clip.purpose for clip in clips
    }
    config_hash = stable_hash(asdict(cfg))
    from ..editorial_evaluation import build_retention_decisions

    decisions = build_retention_decisions(
        [asdict(event) for event in retention],
        clip_purposes=clip_purposes,
        config_hash=config_hash,
        policy_version="2.0.0-semantic",
        planner_version="3.0.0",
    )
    from ..v3_scores import V3ScoreBundle

    score_bundle = V3ScoreBundle.from_blueprint(
        technical_validity=None,
        creative_quality=float(quality.score),
        metrics=metrics,
    )
    platform_value = (
        cfg.platform.value
        if hasattr(cfg.platform, "value")
        else str(cfg.platform).strip().lower()
    )
    blueprint = V3Blueprint(
        "3.0.0",
        core,
        selected.value,
        hooks,
        clips,
        music,
        retention,
        thumbnail,
        titles,
        tags,
        description,
        platform_variants(cfg),
        quality,
        metrics,
        list(V3_CAPABILITIES),
        platform=platform_value,
        audience=cfg.audience,
        score_bundle=score_bundle,
        editorial_decisions=decisions,
    )
    validate_blueprint(blueprint)
    return blueprint


def platform_variants(config: Any) -> dict[str, dict[str, Any]]:
    config.validate()
    return {
        key: {
            "width": policy.width,
            "height": policy.height,
            "max_seconds": policy.max_seconds,
            "safe_bottom": policy.safe_bottom,
            "cta": policy.cta,
            "policy_version": policy.version,
        }
        for key, policy in POLICIES.items()
    }


__all__ = [
    "allocate_durations",
    "analyze_core_idea",
    "analyze_music",
    "build_clip_plan",
    "build_retention_map",
    "choose_edit_type",
    "compact_overlay_text",
    "create_blueprint",
    "generate_hooks",
    "heuristic_metrics_for_plan",
    "overlay_for_purpose",
    "platform_variants",
    "unique_overlay",
]
