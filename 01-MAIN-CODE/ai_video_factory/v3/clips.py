"""Clip planning and intentional overlay compaction."""
from __future__ import annotations

import math
from typing import Any


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
            if (delta > 0 and d < maximum - 1e-4)
            or (delta < 0 and d > minimum + 1e-4)
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
    base, alternate = choices.get(purpose, (purpose, purpose))
    if edit_type == EditType.DOCUMENTARY and purpose in {"Evidence", "Payoff"}:
        base = alternate
    return compact_overlay_text(base, max_words)


def unique_overlay(
    purpose: str,
    core: Any,
    max_words: int,
    index: int,
    used: set[str],
    edit_type: Any,
) -> str:
    candidate = overlay_for_purpose(purpose, core, max_words, edit_type)
    if candidate.lower() in used:
        for suffix in (
            "Here is the key",
            "Notice this",
            "One detail matters",
            "This is the moment",
        ):
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
    minimum_count = max(
        len(strategy["purposes"]),
        math.ceil(config.target_seconds / config.max_clip_seconds),
    )
    preferred_count = max(
        len(strategy["purposes"]),
        round(config.target_seconds / 2.7),
    )
    count = min(
        max(minimum_count, preferred_count),
        int(config.target_seconds // config.min_clip_seconds),
    )
    durations = allocate_durations(
        count,
        config.target_seconds,
        config.min_clip_seconds,
        config.max_clip_seconds,
    )
    purposes = list(strategy["purposes"])
    while len(purposes) < count:
        purposes.insert(max(3, len(purposes) - 2), "Escalation")
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
                zero_index + 1,
                round(cursor, 3),
                end,
                purpose,
                core.target_emotion,
                styles[zero_index % len(styles)],
                unique_overlay(
                    purpose,
                    core,
                    config.max_overlay_words,
                    zero_index + 1,
                    used,
                    edit_type,
                ),
                motions[zero_index % len(motions)],
                transitions[zero_index % len(transitions)],
            )
        )
        cursor = end
    return beats


__all__ = [
    "allocate_durations",
    "build_clip_plan",
    "compact_overlay_text",
    "overlay_for_purpose",
    "unique_overlay",
]
