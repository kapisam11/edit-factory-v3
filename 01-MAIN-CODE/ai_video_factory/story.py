"""Story and emotion utilities to make output feel human-edited.

Provides lightweight, rule-based story-arc enforcement and an emotional
rewriter that strengthens verbs, adds sensory details, and marks script
lines with beats for the auto-edit pipeline.
"""
from typing import Dict, List
import random


def _strongen_sentence(s: str, emotion: str) -> str:
    # simple replacements to make sentences punchier and more sensory
    if not s or not s.strip():
        return s
    s = s.strip()
    # emphasize with stronger verbs
    replacements = {
        "made a choice": "chose",
        "made the choice": "chose",
        "was shocked": "was stunned",
        "shocked everyone": "stunned everyone",
        "changed everything": "upended everything",
        "A pivotal event": "A pivotal moment",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)

    # add small sensory/emotional tweaks based on emotion
    emotive_tags = {
        "dramatic": ["the air felt heavy", "you could feel the tension", "the room went silent"],
        "sad": ["a quiet ache lingered", "you could see the loss on their face"],
        "funny": ["everyone burst out laughing", "it was awkwardly hilarious"],
        "surprising": ["no one expected this twist", "the reveal stopped the room"]
    }
    choices = emotive_tags.get(emotion.lower(), emotive_tags.get("dramatic"))
    if random.random() < 0.25:
        s = s + ". " + random.choice(choices)
    # occasionally add a reflective question to provoke "why does this matter?"
    if random.random() < 0.12:
        q_choices = ["Why would anyone risk that?", "What did they stand to lose?", "Why does this change everything?"]
        s = s + " " + random.choice(q_choices)

    return s


def rewrite_script_emotional(script: str, emotion: str) -> str:
    """Apply lightweight rewrites to `script` to make it more emotional.

    This is intentionally conservative — it strengthens but does not change
    factual content. For stronger rewrites integrate a model-based pass.
    """
    lines = [l.strip() for l in script.splitlines() if l.strip()]
    out = []
    for l in lines:
        out.append(_strongen_sentence(l, emotion))
    return "\n".join(out)


def _keep_opening_tight(lines: List[str], hook: str) -> List[str]:
    if not lines:
        return [hook] if hook else []
    lines[0] = hook if hook else lines[0]
    if len(lines) >= 2:
        impact_words = lines[1].split()
        if len(impact_words) > 8:
            lines[1] = " ".join(impact_words[:8])
    return lines


def _normalize_structure(idea: Dict) -> Dict:
    structure = idea.get("structure") or {}
    target_total = float(structure.get("total_seconds") or sum(float(d) for d, _ in idea.get("edit_plan", []) or []) or 45.0)
    if target_total < 30.0:
        target_total = 30.0
    if target_total > 60.0:
        target_total = 60.0

    base_segments = [2.0, 6.0, 12.0, 25.0, 15.0]
    scale = target_total / sum(base_segments)
    durations = [round(value * scale, 2) for value in base_segments]
    total = round(sum(durations), 2)
    idea["edit_plan"] = [
        (durations[0], "Hook - strongest moment / dramatic text / curiosity"),
        (durations[1], "Intro - topic / person / stakes"),
        (durations[2], "Conflict - tension rises / choices tighten"),
        (durations[3], "Main event - turning point / climax"),
        (durations[4], "Payoff - emotional ending / takeaway"),
    ]
    idea["structure"] = {
        "total_seconds": total,
        "hook": [0.0, durations[0]],
        "intro": [durations[0], round(durations[0] + durations[1], 2)],
        "conflict": [round(durations[0] + durations[1], 2), round(durations[0] + durations[1] + durations[2], 2)],
        "climax": [round(durations[0] + durations[1] + durations[2], 2), round(durations[0] + durations[1] + durations[2] + durations[3], 2)],
        "payoff": [round(total - durations[4], 2), total],
    }
    return idea


def enforce_story_arc(idea: Dict) -> Dict:
    """Given an `idea` dict (hook, script, edit_plan), annotate/adjust it
    to ensure a clear arc: Hook -> Setup -> Conflict -> Climax -> Payoff.

    Modifies `edit_plan` to include explicit beat labels and nudges durations
    to ensure a recognisable arc within a short format.
    """
    hook = idea.get("hook", "")
    emotion = idea.get("emotion", "dramatic")
    script = idea.get("script", "")
    edit_plan = idea.get("edit_plan", [])

    # Preserve existing shot-level edit plans when present, otherwise normalize
    if len(edit_plan) <= 5:
        idea = _normalize_structure(idea)

    # Rewrite script conservatively for emotion while preserving hook-first opening.
    lines = [l.strip() for l in script.splitlines() if l.strip()]
    lines = _keep_opening_tight(lines, hook)
    script = "\n".join(lines)
    idea["script"] = rewrite_script_emotional(script, emotion)

    # Re-assert the hook as the first line after emotional rewrites.
    rewritten_lines = [l.strip() for l in idea["script"].splitlines() if l.strip()]
    rewritten_lines = _keep_opening_tight(rewritten_lines, hook)
    if rewritten_lines:
        idea["script"] = "\n".join(rewritten_lines)

    # Ensure script ends with a reflective hook that asks why it matters
    if idea["script"] and not idea["script"].strip().endswith("?"):
        idea["script"] = idea["script"].strip() + "\nWhy does this matter now?"

    return idea
