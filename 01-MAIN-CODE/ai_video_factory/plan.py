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
    v3_directives = summary.get("v3_directives")
    if isinstance(v3_directives, dict) and v3_directives.get("blueprint_contract"):
        try:
            target_total_seconds = float(summary.get("target_total_seconds", 30.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("target_seconds must be a number") from exc
        if target_total_seconds != target_total_seconds or target_total_seconds < 8.0 or target_total_seconds > 180.0:
            raise ValueError("V3 target_seconds must be between 8 and 180 seconds")
    else:
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

    # Prefer the specialist AI plan when it passed strict schema validation.
    thinking_value = summary.get("video_thinking_plan")
    thinking = thinking_value if isinstance(thinking_value, dict) else {}
    strongest = summary.get("strongest_angle") or summary.get("viral_title") or ""
    fallback_hook = _clean_words(topic, 5)
    if len(fallback_hook.split()) < 2:
        fallback_hook = _clean_words(f"Why {topic} matters", 5)
    hook_source = str(thinking.get("hook") or "").strip() if thinking else ""
    if hook_source and 2 <= len(hook_source.split()) <= 5:
        hook = _normalize_hook(hook_source, fallback_hook)
    else:
        words = [w for w in str(strongest).replace("-", " ").split() if w.isalpha()]
        hook = _normalize_hook(" ".join(words[:4]) if words else fallback_hook, fallback_hook)

    raw_thinking_emotion = str(thinking.get("emotion") or "").strip().lower()
    if raw_thinking_emotion in allowed_emotions:
        emotion = raw_thinking_emotion

    extra_tag = summary.get("content_type", "Story")
    fallback_titles = [
        summary.get("viral_title", f"{hook.title()} - {topic}"),
        f"{hook.title()} - The {extra_tag} Behind {topic}",
        f"Why {topic} Changed Everything",
    ]
    candidate_titles = thinking.get("title_options") if isinstance(thinking.get("title_options"), list) else []
    title_options = list(dict.fromkeys(
        str(value).strip()[:100]
        for value in [*candidate_titles, *fallback_titles]
        if isinstance(value, str) and str(value).strip()
    ))
    try:
        from .human_style_guard import sanitize_public_metadata
        title_options = [
            sanitize_public_metadata(value, "", [])["title"]
            for value in title_options
        ]
        title_options = list(dict.fromkeys(value for value in title_options if value))
    except Exception:
        pass
    if not title_options:
        title_options = [f"{hook} - {topic}"[:100]]

    thinking_lines = thinking.get("script_lines")
    if isinstance(thinking_lines, list) and len(thinking_lines) >= 6 and all(
        isinstance(line, str) and line.strip() for line in thinking_lines
    ):
        script_lines = [str(line).strip() for line in thinking_lines]
        script_lines[0] = f"{hook}."
        video_thinking_applied = True
    else:
        # Deterministic fallback stays topic-grounded and never relies on a
        # library of interchangeable viral hooks or canned ending claims.
        script_lines: List[str] = [f"{hook}.", _build_opening_line(summary, topic)]
        mc = str(summary.get("main_conflict") or "").strip()
        why_care = str(summary.get("why_care") or "").strip()
        payoff = str(summary.get("payoff") or "").strip()
        for line in (mc, why_care, payoff):
            if line and line not in script_lines:
                script_lines.append(line)
        video_thinking_applied = False
        script_lines = [s for s in script_lines if s]

    # Build a fixed five-part structure that scales to the selected total length.
    base_segments = [2.0, 6.0, 12.0, 25.0, 15.0]
    scale = target_total_seconds / sum(base_segments)
    durations = [round(value * scale, 2) for value in base_segments]

    learned_value = summary.get("learned_editor_preferences") or summary.get("recommended_settings") or {}
    learned_settings = learned_value if isinstance(learned_value, dict) else {}
    learned_cuts_per_minute: Optional[float] = None
    try:
        candidate_cpm = float(learned_settings.get("cuts_per_minute"))
        if 0.1 <= candidate_cpm <= 240.0:
            learned_cuts_per_minute = candidate_cpm
    except (TypeError, ValueError):
        pass

    try:
        learned_hook = float(learned_settings.get("hook_duration"))
    except (TypeError, ValueError):
        learned_hook = 0.0
    if 0.1 <= learned_hook <= min(6.0, target_total_seconds * 0.30):
        original_rest = sum(durations[1:])
        rest_target = target_total_seconds - learned_hook
        durations = [learned_hook] + [
            round(value * rest_target / max(0.01, original_rest), 2)
            for value in durations[1:]
        ]
        durations[-1] = round(durations[-1] + target_total_seconds - sum(durations), 2)
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
    if learned_cuts_per_minute is not None:
        # Convert the learned cut rate into a bounded number of actual beats,
        # then distribute them over the story arc instead of only storing the
        # recommendation in metadata.
        max_target_cuts = min(120, max(5, int(target_total_seconds / 0.5)))
        target_cuts = max(5, min(max_target_cuts, int(round(target_total_seconds * learned_cuts_per_minute / 60.0))))
        phase_durations = [item[0] for item in beat_definitions]
        remaining_cuts = target_cuts - len(beat_definitions)
        total_phase_duration = sum(phase_durations) or target_total_seconds
        exact_extras = [remaining_cuts * value / total_phase_duration for value in phase_durations]
        extras = [int(value) for value in exact_extras]
        missing = remaining_cuts - sum(extras)
        order = sorted(range(len(exact_extras)), key=lambda index: exact_extras[index] - extras[index], reverse=True)
        for index in order[:missing]:
            extras[index] += 1
        for (segment_duration, labels), extra_count in zip(beat_definitions, extras):
            count = 1 + extra_count
            base_duration = segment_duration / count
            for index in range(count):
                edit_plan.append((round(base_duration, 2), labels[index % len(labels)]))
        if edit_plan:
            edit_plan[-1] = (
                round(edit_plan[-1][0] + duration_total - sum(value for value, _label in edit_plan), 2),
                edit_plan[-1][1],
            )
    else:
        for segment_duration, labels in beat_definitions:
            edit_plan.extend(_subdivide_segment(segment_duration, labels))

    directions = thinking.get("edit_directions")
    if isinstance(directions, dict):
        phase_map = {
            "hook": "hook",
            "intro": "setup",
            "conflict": "conflict",
            "main event": "climax",
            "payoff": "payoff",
        }
        directed_plan: List[Tuple[float, str]] = []
        for duration, label in edit_plan:
            phase = label.split(" - ", 1)[0].strip().lower()
            directive = directions.get(phase_map.get(phase, ""))
            if isinstance(directive, str) and directive.strip():
                directed_plan.append((duration, f"{phase.title()} - {directive.strip()}"))
            else:
                directed_plan.append((duration, label))
        edit_plan = directed_plan

    script_text = "\n".join(script_lines)

    idea = {
        "hook": hook,
        "emotion": emotion,
        "mood": emotion,
        "title": title_options[0],
        "title_options": title_options,
        "script": script_text,
        "edit_plan": edit_plan,
        "video_thinking_applied": video_thinking_applied,
        "creative_directives": {
            "angle": str(thinking.get("angle") or ""),
            "why_people_care": str(thinking.get("why_people_care") or ""),
            "watch_to_end_reason": str(thinking.get("watch_to_end_reason") or ""),
            "payoff": str(thinking.get("payoff") or ""),
            "music_direction": str(thinking.get("music_direction") or ""),
            "thumbnail_concept": str(thinking.get("thumbnail_concept") or ""),
            "learned_music_style": str(learned_settings.get("music_style") or ""),
            "learned_caption_style": str(learned_settings.get("caption_style") or ""),
            "learned_voice": str(learned_settings.get("voice") or ""),
        },
        "structure": {
            "total_seconds": duration_total,
            "hook": [0.0, durations[0]],
            "intro": [durations[0], round(durations[0] + durations[1], 2)],
            "conflict": [round(durations[0] + durations[1], 2), round(durations[0] + durations[1] + durations[2], 2)],
            "climax": [round(durations[0] + durations[1] + durations[2], 2), round(durations[0] + durations[1] + durations[2] + durations[3], 2)],
            "payoff": [round(duration_total - durations[4], 2), duration_total],
        },
    }

    if not idea["video_thinking_applied"]:
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
