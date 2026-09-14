"""Create video idea, hook, script and edit plan."""
from typing import Dict, List, Tuple, Optional, Any
from . import story
from .validation import validate_target_seconds


def _clean_words(text: str, limit: int) -> str:
    words = [word for word in text.split() if word]
    if len(words) > limit:
        words = words[:limit]
    return " ".join(words).rstrip(".")


def _normalize_hook(text: str, fallback: str) -> str:
    hook = _clean_words(text or fallback, 5)
    if len(hook.split()) < 2:
        hook = fallback
    return hook


def _build_opening_line(summary: Dict[str, str], topic: str) -> str:
    strongest = (summary.get("strongest_angle") or "").strip()
    who = (summary.get("who_what") or "").strip()
    conflict = (summary.get("main_conflict") or "").strip()
    for candidate in (strongest, conflict, who):
        if candidate:
            return _clean_words(candidate, 8)
    return _clean_words(f"Something changed fast in {topic}", 8)


def _subdivide_segment(duration: float, labels: List[str], min_shot: float = 1.0, max_shot: float = 3.0) -> List[Tuple[float, str]]:
    if duration <= max_shot:
        return [(round(duration, 2), labels[0])]

    count = max(1, int(round(duration / 2.0)))
    while duration / count > max_shot:
        count += 1
    while count > 1 and duration / count < min_shot:
        count -= 1

    shot_duration = round(duration / count, 2)
    shots: List[Tuple[float, str]] = []
    for i in range(count):
        shots.append((shot_duration, labels[i % len(labels)]))

    current_total = round(sum(d for d, _ in shots), 2)
    diff = round(duration - current_total, 2)
    if abs(diff) >= 0.01:
        shots[-1] = (round(shots[-1][0] + diff, 2), shots[-1][1])

    return shots


def make_idea(summary: Dict[str, str]) -> Dict[str, object]:
    """Generate hook, title options, script draft and an edit plan.

    The outputs are intentionally concise so they can be used in a short
    vertical edit. Duration follows the shared application contract of 15-120s.
    """
    topic = summary.get("topic", "Unknown topic")
    target_total_seconds = validate_target_seconds(summary.get("target_total_seconds", 45.0))

    # Pick ONE main emotion from research if present; otherwise choose a sensible default
    allowed_emotions = ["emotional", "inspiring", "nostalgic", "dramatic", "mysterious", "funny", "shocking", "intense"]
    raw_em = (summary.get("emotion") or "").lower()
    emotion = "dramatic"
    if raw_em:
        for a in allowed_emotions:
            if a in raw_em:
                emotion = a
                break

    # Create a concise human-style hook (2-5 words) using heuristics
    hook_candidates = [
        "Nobody believed him",
        "The hidden legend",
        "His final choice",
        "Lost forever",
        "The real reason",
    ]
    strongest = summary.get("strongest_angle") or summary.get("viral_title") or ""
    if strongest:
        words = [w for w in strongest.replace("-", " ").split() if w.isalpha()]
        hook = _normalize_hook(" ".join(words[:4]) if words else hook_candidates[0], hook_candidates[0])
    else:
        hook = hook_candidates[0]
    hook = _normalize_hook(hook, hook_candidates[0])

    extra_tag = summary.get("content_type", "Story")
    title_options = [
        summary.get("viral_title", f"{hook.title()} - {topic}"),
        f"{hook.title()} - The {extra_tag} Behind {topic}",
        f"Why {topic} Changed Everything",
    ]

    script_lines: List[str] = []
    script_intro = f"{hook}."
    script_lines.append(script_intro)
    script_lines.append(_build_opening_line(summary, topic))
    mc = summary.get("main_conflict", "A pivotal moment changes everything.")
    if mc:
        script_lines.append(mc)
    if summary.get("why_care"):
        script_lines.append(summary.get("why_care"))
    script_lines.append("The stakes rose fast and every second mattered.")
    script_lines.append("That choice split friends and rivals apart.")
    script_lines.append("The payoff hits hardest at the end.")
    script_lines.append("Why does this matter now?")
    script_lines = [s for s in script_lines if s]

    # Build a fixed five-part structure that scales to the selected total length.
    base_segments = [2.0, 6.0, 12.0, 25.0, 15.0]
    scale = target_total_seconds / sum(base_segments)
    durations = [round(value * scale, 2) for value in base_segments]
    duration_total = round(sum(durations), 2)

    beat_definitions = [
        (
            durations[0],
            [
                "Hook - strongest moment / jump cut / impact frame / quick zoom",
                "Hook - immediate shock / focus pull / strong text",
            ],
        ),
        (
            durations[1],
            [
                "Intro - topic / person / stakes / camera move",
                "Intro - urgency / why care / subtle motion",
            ],
        ),
        (
            durations[2],
            [
                "Conflict - tension rises / motion blur / subtle shake",
                "Conflict - stakes escalate / jump cut / quick zoom",
                "Conflict - twist revealed / raw emotion / fast cut",
            ],
        ),
        (
            durations[3],
            [
                "Main event - turning point / climax / speed ramp",
                "Main event - escalation / impact frame / cinematic transition",
                "Main event - emotional peak / punchy zoom",
            ],
        ),
        (
            durations[4],
            [
                "Payoff - emotional ending / takeaway / soft settle",
                "Payoff - reaction / call to action / cinematic transition",
            ],
        ),
    ]

    edit_plan: List[Tuple[float, str]] = []
    for segment_duration, labels in beat_definitions:
        edit_plan.extend(_subdivide_segment(segment_duration, labels))

    script_text = "\n".join(script_lines)

    idea = {
        "hook": hook,
        "emotion": emotion,
        "title_options": title_options,
        "script": script_text,
        "edit_plan": edit_plan,
        "structure": {
            "total_seconds": duration_total,
            "hook": [0.0, durations[0]],
            "intro": [durations[0], round(durations[0] + durations[1], 2)],
            "conflict": [round(durations[0] + durations[1], 2), round(durations[0] + durations[1] + durations[2], 2)],
            "climax": [round(durations[0] + durations[1] + durations[2], 2), round(durations[0] + durations[1] + durations[2] + durations[3], 2)],
            "payoff": [round(duration_total - durations[4], 2), duration_total],
        },
    }

    try:
        idea = story.enforce_story_arc(idea)
    except Exception:
        pass

    return idea


def make_idea_with_knowledge(
    summary: Dict[str, str],
    topic: str,
    topic_expertise: Optional[Dict[str, Any]] = None,
    trending: Optional[Dict[str, Any]] = None,
) -> Dict[str, object]:
    """Generate idea with topic expertise and trending knowledge."""
    idea = make_idea(summary)

    if not topic_expertise:
        topic_expertise = {}

    if not trending:
        trending = {}

    if topic_expertise:
        idea["topic_expertise"] = topic_expertise
        idea["topic"] = topic
        if "tone" in topic_expertise:
            idea["tone"] = topic_expertise["tone"]
        if "typical_duration" in topic_expertise:
            idea["typical_duration"] = topic_expertise["typical_duration"]
        if "key_elements" in topic_expertise:
            idea["key_elements"] = topic_expertise["key_elements"]
        if "best_hooks" in topic_expertise:
            idea["best_hooks"] = topic_expertise["best_hooks"]

    if trending:
        idea["trending_data"] = trending
        if "optimal_cuts_per_minute" in trending:
            total_sec = idea["structure"].get("total_seconds", 45)
            target_cuts = int((trending["optimal_cuts_per_minute"] / 60.0) * total_sec)
            idea["target_cuts"] = target_cuts
        if "retention_techniques" in trending:
            idea["retention_techniques"] = trending["retention_techniques"]

    return idea
