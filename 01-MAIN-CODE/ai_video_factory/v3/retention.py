"""Semantic retention planning module."""
from __future__ import annotations

from typing import Any


def build_retention_map(config: Any, clips: list[Any], music: Any) -> list[Any]:
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
        timestamp = round(
            max(0.0, min(float(time_value), config.target_seconds - 0.01)),
            3,
        )
        close_indexes = [
            index for index, existing in enumerate(events)
            if abs(existing.time - timestamp) < 0.35
        ]
        if close_indexes:
            strongest_index = max(
                close_indexes,
                key=lambda index: events[index].confidence,
            )
            if confidence <= events[strongest_index].confidence:
                return
            for index in reversed(close_indexes):
                events.pop(index)
        events.append(
            RetentionEvent(
                timestamp,
                kind,
                (
                    f"{reason.replace('_', ' ').capitalize()}: apply a restrained "
                    "visual treatment while preserving story continuity."
                ),
                reason,
                min(1.0, confidence),
                min(1.0, confidence),
            )
        )

    first = clips[0]
    kind, reason, confidence = purpose_kind.get(
        first.purpose,
        ("zoom", "HOOK_ESTABLISHMENT", 0.76),
    )
    add_event(first.start, kind, reason, confidence)
    for clip in clips[1:]:
        mapped = purpose_kind.get(clip.purpose)
        if mapped is not None:
            add_event(clip.start, *mapped)
    payoff_candidates = [
        clip.start
        for clip in clips
        if clip.purpose in {"Climax", "Payoff", "Punchline", "Final impact"}
    ]
    if payoff_candidates:
        nearest = min(
            payoff_candidates,
            key=lambda value: abs(value - music.drop_time),
        )
        if abs(nearest - music.drop_time) <= max(
            0.75, config.retention_interval * 0.5
        ):
            add_event(nearest, "beat drop", "PAYOFF_ALIGNMENT", 0.96)
    final_clip = clips[-1]
    if final_clip.purpose in {"Final impact", "Payoff", "Reaction"} and not any(
        abs(event.time - final_clip.start) < 0.35 for event in events
    ):
        add_event(final_clip.start, "text", "FINAL_IMPACT", 0.86)
    return sorted(events, key=lambda event: event.time)


__all__ = ["build_retention_map"]
