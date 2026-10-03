"""V3 creative planning implementation.

The legacy v3_engine module exposes compatibility facades, while this module
owns the actual planning behavior.
"""
from __future__ import annotations

from dataclasses import asdict
import math
import re
from typing import Any, Mapping, Sequence

from .audience import parse_audience
from .hook_eval import evaluate_hook_candidates, generate_hook_candidates
from ..idempotency import stable_hash
from .platform_policy import POLICIES
from ..v3_scoring import heuristic_metrics


def analyze_core_idea(topic: str, context: str = "", audience: str = "general short-form viewers") -> Any:
    from ..v3_engine import CoreIdea
    topic = str(topic).strip()
    context = str(context).strip()
    if not topic:
        raise ValueError("topic is required")
    scores = __import__("ai_video_factory.v3_semantics", fromlist=["combined_scores"]).combined_scores(topic, context)
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
    return CoreIdea(topic, care, angle, emotion, f"The viewer needs the final proof behind: {angle}.", payoff, stakes)


def choose_edit_type(core: Any, requested: str | None = None) -> Any:
    from ..v3_engine import EditType, EDIT_BY_EMOTION
    if requested:
        for item in EditType:
            if item.value.lower() == str(requested).strip().lower():
                return item
        raise ValueError(f"unknown edit type: {requested}")
    return EDIT_BY_EMOTION.get(core.target_emotion, EditType.STORYTELLING)


def generate_hooks(core: Any, edit_type: Any, *, source_evidence: Mapping[str, Any] | None = None) -> list[Any]:
    from ..v3_engine import HookPack
    evaluated = evaluate_hook_candidates(
        generate_hook_candidates(
            core.topic, core.stakes, core.emotional_angle,
            core.watch_to_end_reason, edit_type.value,
        ),
        core.topic, core.stakes, core.emotional_angle, core.watch_to_end_reason,
        source_evidence=source_evidence,
        audience=parse_audience(str((source_evidence or {}).get("audience_label", ""))).to_dict(),
    )
    return [
        HookPack(
            candidate.visual,
            candidate.text,
            candidate.emotional_reason,
            evaluation.score,
            evaluation=evaluation.to_dict(),
            score_semantics="editorial_heuristic_score",
        )
        for candidate, evaluation in evaluated
    ]


def compact_overlay_text(text: str, max_words: int = 6) -> str:
    return " ".join(str(text).strip().split()[:max_words])


def allocate_durations(count: int, total: float, minimum: float, maximum: float) -> list[float]:
    if count <= 0 or total < minimum * count or total > maximum * count:
        raise ValueError("clip count cannot satisfy duration bounds")
    weights = [1.0] * count
    weights[0] = 0.72
    if count > 1:
        weights[-1] = 1.20
    if count > 3:
        weights[-2] = 1.10
    durations = [min(maximum, max(minimum, total * w / sum(weights))) for w in weights]
    for _ in range(200):
        delta = total - sum(durations)
        if abs(delta) < 0.00025:
            break
        candidates = [
            i for i, d in enumerate(durations)
            if (delta > 0 and d < maximum - 1e-4) or (delta < 0 and d > minimum + 1e-4)
        ]
        if not candidates:
            break
        step = delta / len(candidates)
        for i in candidates:
            durations[i] = min(maximum, max(minimum, durations[i] + step))
    rounded = [round(d, 3) for d in durations]
    rounded[-1] = round(rounded[-1] + total - sum(rounded), 3)
    if abs(sum(rounded) - total) > 0.01 or not minimum <= rounded[-1] <= maximum:
        raise ValueError("duration allocation could not satisfy exact target")
    return rounded


def overlay_for_purpose(purpose: str, core: Any, max_words: int, edit_type: Any) -> str:
    from ..v3_engine import EditType
    choices = {
        "Hook": ("Not what you expected", f"Nobody saw {core.topic} coming"),
        "Context": (f"It started with {core.topic}", "Here is the setup"),
        "Setup": ("Watch what happens", "This is where it starts"),
        "Curiosity": ("But one detail mattered", "There was one problem"),
        "Question": ("Something was missing", "The real question was this"),
        "Claim": ("There is a reason", "This tells us something"),
        "Memory": ("You remember this", "That moment still hits"),
        "Contrast": ("Then everything changed", "Before vs after"),
        "Trait": ("Notice what he does", "That is the pattern"),
        "Evidence": ("Look at this detail", "The evidence is here"),
        "Importance": ("This raised the stakes", "Now it matters"),
        "Threat": ("Then it got dangerous", "The risk was real"),
        "Escalation": ("The pressure kept rising", "Everything accelerated"),
        "Climax": ("This was the moment", "Now it all lands"),
        "Payoff": ("This was the proof", "Here is the answer"),
        "Punchline": ("And then this happened", "That was the joke"),
        "Reaction": ("Watch the reaction", "That face says it all"),
        "Proof": ("Here is the proof", "This confirms it"),
        "Final impact": (core.payoff, "That is why it mattered"),
    }
    choice = choices.get(purpose, (purpose, purpose))
    base, alternate = choice
    if edit_type == EditType.DOCUMENTARY and purpose in {"Evidence", "Payoff"}:
        base = alternate
    return compact_overlay_text(base, max_words)


def unique_overlay(purpose: str, core: Any, max_words: int, index: int, used: set[str], edit_type: Any) -> str:
    candidate = overlay_for_purpose(purpose, core, max_words, edit_type)
    if candidate.lower() in used:
        for suffix in ("Here is the key", "Notice this", "One detail matters", "This is the moment"):
            trial = compact_overlay_text(suffix, max_words)
            if trial.lower() not in used:
                candidate = trial
                break
        else:
            candidate = compact_overlay_text(f"Beat {index} {core.topic}", max_words)
    used.add(candidate.lower())
    return candidate


def build_clip_plan(core: Any, edit_type: Any, config: Any) -> list[Any]:
    from ..v3_engine import ClipBeat, EDIT_STRATEGIES
    config.validate()
    strategy = EDIT_STRATEGIES[edit_type]
    minimum_count = max(len(strategy["purposes"]), math.ceil(config.target_seconds / config.max_clip_seconds))
    preferred_count = max(len(strategy["purposes"]), round(config.target_seconds / 2.7))
    count = min(max(minimum_count, preferred_count), int(config.target_seconds // config.min_clip_seconds))
    durations = allocate_durations(count, config.target_seconds, config.min_clip_seconds, config.max_clip_seconds)
    purposes = list(strategy["purposes"])
    while len(purposes) < count:
        insert_at = max(3, len(purposes) - 2)
        purposes.insert(insert_at, "Escalation")
    purposes = purposes[:count]
    motions = list(strategy["motions"])
    transitions = list(strategy["transitions"])
    styles = list(strategy["visual_styles"])
    beats: list[Any] = []
    cursor = 0.0
    used: set[str] = set()
    for zero_index, (purpose, duration) in enumerate(zip(purposes, durations)):
        end = round(cursor + duration, 3)
        beats.append(
            ClipBeat(
                zero_index + 1, round(cursor, 3), end, purpose, core.target_emotion,
                styles[zero_index % len(styles)],
                unique_overlay(purpose, core, config.max_overlay_words, zero_index + 1, used, edit_type),
                motions[zero_index % len(motions)],
                transitions[zero_index % len(transitions)],
            )
        )
        cursor = end
    return beats


def analyze_music(core: Any, config: Any, clips: Sequence[Any]) -> Any:
    from ..v3_engine import MusicPlan
    beat_seconds = round(60.0 / config.bpm, 4)
    payoff_start = clips[-2].start if len(clips) >= 2 else config.target_seconds * 0.7
    drop = min(config.target_seconds * 0.72, max(2.0, payoff_start))
    sync = []
    t = 0.0
    while t < config.target_seconds - 1e-6:
        sync.append(round(t, 3))
        t += beat_seconds * 2
    sync.append(round(config.target_seconds, 3))
    return MusicPlan(
        config.bpm,
        "high" if core.target_emotion in {"dramatic", "inspiring", "funny"} else "medium",
        core.target_emotion,
        beat_seconds,
        round(drop, 3),
        sync,
    )


def build_retention_map(config: Any, clips: Sequence[Any], music: Any) -> list[Any]:
    from ..v3_engine import RetentionEvent
    config.validate()
    if not clips:
        return []
    purpose_kind = {
        "Hook": ("zoom", "HOOK_ESTABLISHMENT", 0.80),
        "Setup": ("motion", "STORY_SETUP", 0.70),
        "Build": ("motion", "ESCALATION", 0.80),
        "Conflict": ("motion", "CONFLICT_EMPHASIS", 0.82),
        "Climax": ("beat drop", "PAYOFF_ALIGNMENT", 0.94),
        "Payoff": ("beat drop", "PAYOFF_ALIGNMENT", 0.96),
        "Punchline": ("beat drop", "PAYOFF_ALIGNMENT", 0.94),
        "Reaction": ("angle", "REACTION_EMPHASIS", 0.78),
        "Context": ("motion", "CONTEXT_SUPPORT", 0.65),
        "Question": ("text", "COMPREHENSION_SUPPORT", 0.70),
        "Claim": ("text", "CLAIM_EMPHASIS", 0.70),
        "Curiosity": ("text", "COMPREHENSION_SUPPORT", 0.70),
        "Threat": ("zoom", "THREAT_EMPHASIS", 0.75),
        "Escalation": ("motion", "ESCALATION", 0.80),
        "Evidence": ("text", "EVIDENCE_SUPPORT", 0.75),
        "Proof": ("text", "EVIDENCE_SUPPORT", 0.75),
        "Memory": ("motion", "MEMORY_EMPHASIS", 0.68),
        "Contrast": ("angle", "CONTRAST_EMPHASIS", 0.72),
        "Trait": ("angle", "TRAIT_EMPHASIS", 0.72),
        "Final impact": ("text", "FINAL_IMPACT", 0.86),
    }
    events: list[Any] = []

    def add_event(time_value: float, kind: str, reason: str, confidence: float) -> None:
        timestamp = round(max(0.0, min(float(time_value), config.target_seconds - 0.01)), 3)
        close_indexes = [index for index, existing in enumerate(events) if abs(existing.time - timestamp) < 0.35]
        if close_indexes:
            strongest_index = max(close_indexes, key=lambda index: events[index].confidence)
            if confidence <= events[strongest_index].confidence:
                return
            for index in reversed(close_indexes):
                events.pop(index)
        events.append(
            RetentionEvent(
                timestamp,
                kind,
                f"{reason.replace('_', ' ').capitalize()}: apply a restrained visual treatment while preserving story continuity.",
                reason,
                min(1.0, confidence),
                min(1.0, confidence),
            )
        )

    first = clips[0]
    kind, reason, confidence = purpose_kind.get(first.purpose, ("zoom", "HOOK_ESTABLISHMENT", 0.76))
    add_event(first.start, kind, reason, confidence)
    for clip in clips[1:]:
        mapped = purpose_kind.get(clip.purpose)
        if mapped is not None:
            add_event(clip.start, *mapped)
    payoff_candidates = [
        clip.start for clip in clips
        if clip.purpose in {"Climax", "Payoff", "Punchline", "Final impact"}
    ]
    if payoff_candidates:
        nearest = min(payoff_candidates, key=lambda value: abs(value - music.drop_time))
        if abs(nearest - music.drop_time) <= max(0.75, config.retention_interval * 0.5):
            add_event(nearest, "beat drop", "PAYOFF_ALIGNMENT", 0.96)
    final_clip = clips[-1]
    if final_clip.purpose in {"Final impact", "Payoff", "Reaction"} and not any(
        abs(event.time - final_clip.start) < 0.35 for event in events
    ):
        add_event(final_clip.start, "text", "FINAL_IMPACT", 0.86)
    return sorted(events, key=lambda event: event.time)


def heuristic_metrics_for_plan(core: Any, hooks: Sequence[Any], clips: Sequence[Any], quality: Any) -> dict[str, float]:
    hook = hooks[0].score if hooks else 0.0
    avg = clips[-1].end / len(clips) if clips else 0.0
    pace = min(1.0, 2.8 / max(avg, 0.1))
    q = quality.score / 100.0
    emotion = 0.9 if core.target_emotion in {"trust", "dramatic", "inspiring", "nostalgic"} else 0.82
    return heuristic_metrics(hook=hook, pace=pace, quality=q, emotion=emotion)


def create_blueprint(
    topic: str,
    *,
    context: str = "",
    config: Any | None = None,
    edit_type: str | None = None,
    footage_evidence: Mapping[str, Any] | None = None,
) -> Any:
    from ..v3_engine import (
        V3Config, V3Blueprint, ClipBeat, ClipEvidence, V3_CAPABILITIES,
        automated_editorial_checks, validate_blueprint,
    )
    cfg = config or V3Config()
    cfg.validate()
    core = analyze_core_idea(topic, context, cfg.audience)
    selected = choose_edit_type(core, edit_type)
    evidence_context = dict(footage_evidence or {})
    evidence_context["audience_label"] = cfg.audience
    hooks = generate_hooks(core, selected, source_evidence=evidence_context)
    clips = build_clip_plan(core, selected, cfg)

    if footage_evidence:
        from .clip_evidence import build_clip_evidence
        evidence_rows = build_clip_evidence(
            [asdict(item) for item in clips],
            dict(footage_evidence),
            source_asset=str(dict(footage_evidence).get("source_asset", "")),
        )
        by_index = {int(row.get("clip_index", 0)): row for row in evidence_rows}
        enriched: list[Any] = []
        for clip in clips:
            row = by_index.get(clip.index, {})
            enriched.append(
                ClipBeat(
                    clip.index, clip.start, clip.end, clip.purpose, clip.emotion,
                    clip.visual_style, clip.text_overlay, clip.camera_motion, clip.transition,
                    ClipEvidence(
                        source_asset=str(row.get("source_asset", "")),
                        source_start=float(row.get("source_start", 0.0)),
                        source_end=float(row.get("source_end", 0.0)),
                        semantic_tags=tuple(str(x) for x in row.get("semantic_tags", ())),
                        evidence_score=float(row.get("evidence_score", 0.0)),
                        evidence_status=str(row.get("status", "unsupported")),
                    ),
                )
            )
        clips = enriched

    music = analyze_music(core, cfg, clips)
    retention = build_retention_map(cfg, clips, music)
    thumbnail, titles, tags, description = (
        _planned_metadata(core, parse_audience(cfg.audience))
    )
    quality = automated_editorial_checks(core, selected, hooks, clips, retention, cfg)
    metrics = heuristic_metrics_for_plan(core, hooks, clips, quality)
    clip_purposes = {round(clip.start, 3): clip.purpose for clip in clips}
    config_hash = stable_hash(asdict(cfg))
    decisions = __import__(
        "ai_video_factory.editorial_evaluation",
        fromlist=["build_retention_decisions"],
    ).build_retention_decisions(
        [asdict(event) for event in retention],
        clip_purposes=clip_purposes,
        config_hash=config_hash,
        policy_version="2.0.0-semantic",
        planner_version="3.0.0",
    )
    score_bundle = __import__(
        "ai_video_factory.v3_scores", fromlist=["V3ScoreBundle"]
    ).V3ScoreBundle.from_blueprint(
        technical_validity=None,
        creative_quality=float(quality.score),
        metrics=metrics,
    )
    platform_value = cfg.platform.value if hasattr(cfg.platform, "value") else str(cfg.platform).strip().lower()
    blueprint = V3Blueprint(
        "3.0.0", core, selected.value, hooks, clips, music, retention,
        thumbnail, titles, tags, description, platform_variants(cfg), quality,
        metrics, list(V3_CAPABILITIES), platform=platform_value, audience=cfg.audience,
        score_bundle=score_bundle, editorial_decisions=decisions,
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


def _planned_metadata(core: Any, audience_profile: Any | None = None) -> tuple[str, list[str], list[str], str]:
    topic = compact_overlay_text(core.topic, 8)
    angle = compact_overlay_text(core.emotional_angle, 10)
    payoff = compact_overlay_text(core.payoff, 12)
    emotion = compact_overlay_text(core.target_emotion, 3)
    titles = list(dict.fromkeys([
        compact_overlay_text(f"{topic}: {angle}", 14),
        compact_overlay_text(f"The moment {topic} changed", 10),
        compact_overlay_text(f"What happened with {topic}", 10),
        compact_overlay_text(f"The part of {topic} most people miss", 12),
        compact_overlay_text(f"Why {topic} still matters", 10),
        compact_overlay_text(f"The turning point in {topic}", 11),
        compact_overlay_text(f"{topic}: the detail that changes the story", 12),
        compact_overlay_text(f"How {topic} changed everything", 10),
    ]))
    topic_tags = [token for token in re.findall(r"[A-Za-z0-9]{3,}", core.topic.lower())]
    tags = list(dict.fromkeys([*topic_tags, emotion]))
    description = " ".join(
        part for part in (
            re.sub(r"\s+", " ", core.emotional_angle).strip(),
            re.sub(r"\s+", " ", core.why_people_care).strip(),
            re.sub(r"\s+", " ", payoff).strip(),
        ) if part
    )
    thumbnail = (
        f"Use the strongest focal frame from {topic}, keep the subject clear, "
        f"leave text-safe space, and use a bold 2-5 word hook tied to the story."
    )
    return thumbnail, titles[:10], tags[:12], description


__all__ = [
    "allocate_durations", "analyze_core_idea", "analyze_music",
    "build_clip_plan", "build_retention_map", "choose_edit_type",
    "compact_overlay_text", "create_blueprint", "generate_hooks",
    "heuristic_metrics_for_plan", "overlay_for_purpose", "platform_variants",
    "unique_overlay",
]
