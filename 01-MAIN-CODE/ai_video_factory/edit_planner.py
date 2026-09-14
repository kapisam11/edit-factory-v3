"""Convert a short-form script into a footage-aware edit timeline.

The planner remains backward compatible with the v2 API, while accepting the
v3 creative contract so the selected edit style and retention plan affect the
actual render timeline.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .production_models import EditTimeline, Scene, TimelineSegment
from .scene_intelligence import score_scene
from .validation import validate_target_seconds


ROLE_ORDER = ("hook", "intro", "conflict", "climax", "payoff")
ROLE_EFFECTS = {
    "hook": ["impact", "quick_zoom"],
    "intro": ["subtle_zoom"],
    "conflict": ["jump_cut", "motion"],
    "climax": ["speed_ramp", "impact"],
    "payoff": ["settle"],
}

V3_PURPOSE_ROLE = {
    "Hook": "hook", "Setup": "intro", "Context": "intro", "Claim": "intro", "Question": "intro",
    "Curiosity": "conflict", "Memory": "conflict", "Contrast": "conflict", "Trait": "conflict",
    "Evidence": "conflict", "Threat": "conflict", "Importance": "climax", "Escalation": "climax",
    "Climax": "climax", "Punchline": "payoff", "Payoff": "payoff", "Proof": "payoff",
    "Final impact": "payoff", "Reaction": "payoff",
}


def _tokenize(text: str) -> List[str]:
    return [t.lower().strip(".,!?;:()[]{}\"'") for t in text.split() if t.strip()]


def _overlap_score(a: str, b: str) -> float:
    aa = set(_tokenize(a))
    bb = set(_tokenize(b))
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa)


def _sentences(script: str) -> List[str]:
    lines = [line.strip() for line in script.splitlines() if line.strip()]
    if lines:
        return lines
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]


def _role_for_index(index: int, count: int) -> str:
    if count <= 1:
        return "hook"
    position = index / max(1, count - 1)
    if index == 0:
        return "hook"
    if position < 0.25:
        return "intro"
    if position < 0.55:
        return "conflict"
    if position < 0.82:
        return "climax"
    return "payoff"


def _desired_duration(total: float, index: int, count: int) -> float:
    weights = [1.0 for _ in range(count)]
    if count >= 5:
        weights[0] = 0.8
        weights[-1] = 1.15
        for i in range(1, count - 1):
            if _role_for_index(i, count) == "climax":
                weights[i] = 1.25
    return max(1.0, total * weights[index] / (sum(weights) or 1.0))


def choose_scene(query: str, scenes: Sequence[Scene], used_ids: Iterable[str], desired_seconds: float, role: str) -> Tuple[Scene, float]:
    used = set(used_ids)
    candidates = [s for s in scenes if s.id not in used] or list(scenes)
    if not candidates:
        raise ValueError("No scenes are available for planning")

    ranked: List[Tuple[float, Scene]] = []
    for scene in candidates:
        relevance = _overlap_score(query, scene.searchable_text)
        salience = score_scene(scene, query)
        duration_fit = min(scene.duration, desired_seconds) / max(scene.duration, desired_seconds, 0.01)
        role_bonus = 0.0
        if role == "climax":
            role_bonus = 0.20 * scene.motion_score + 0.20 * scene.importance_score
        elif role == "payoff":
            role_bonus = 0.10 * (1.0 - scene.motion_score) + 0.15 * scene.face_count / max(1, scene.face_count + 1)
        elif role == "hook":
            role_bonus = 0.20 * scene.importance_score
        freshness = 0.10 if scene.id not in used else -0.15
        value = 0.35 * relevance + 0.30 * salience + 0.15 * duration_fit + role_bonus + freshness
        ranked.append((value, scene))
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best_scene = ranked[0]
    return best_scene, round(float(best_score), 4)


def _directive_for_index(directives: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    clip_plan = directives.get("clip_plan", [])
    if isinstance(clip_plan, list) and index < len(clip_plan) and isinstance(clip_plan[index], Mapping):
        return clip_plan[index]
    return {}


def _directive_effects(directive: Mapping[str, Any], role: str) -> List[str]:
    effects = list(ROLE_EFFECTS.get(role, []))
    motion = str(directive.get("camera_motion", "")).lower()
    transition = str(directive.get("transition", "")).lower()
    if motion in {"punch-in", "micro-zoom"}:
        effects.append("quick_zoom")
    elif motion in {"tracking", "reframe", "slow push"}:
        effects.append("camera move")
    elif motion == "subtle-parallax":
        effects.append("soft settle")
    if transition in {"dissolve", "match cut"}:
        effects.append("cinematic transition")
    elif transition == "speed ramp":
        effects.append("speed ramp")
    return list(dict.fromkeys(effects))


def build_timeline(
    script: str,
    scenes: Sequence[Scene],
    *,
    total_seconds: Optional[float] = None,
    aspect_ratio: str = "9:16",
    source_video: Optional[str] = None,
    creative_directives: Optional[Mapping[str, Any]] = None,
) -> EditTimeline:
    """Build a deterministic timeline and honor v3 creative directives when supplied."""
    lines = _sentences(script)
    if not lines:
        raise ValueError("Script is empty")
    if not scenes:
        raise ValueError("Scene index is empty")

    target = validate_target_seconds(
        sum(s.duration for s in scenes[: max(1, len(lines))]), "total_seconds"
    ) if total_seconds is None else validate_target_seconds(total_seconds, "total_seconds")

    directives = creative_directives if isinstance(creative_directives, Mapping) else {}
    cursor = 0.0
    used: List[str] = []
    segments: List[TimelineSegment] = []

    for index, sentence in enumerate(lines):
        directive = _directive_for_index(directives, index)
        purpose = str(directive.get("purpose", "")).strip()
        role = V3_PURPOSE_ROLE.get(purpose) or _role_for_index(index, len(lines))
        desired = _desired_duration(target, index, len(lines))
        try:
            planned = float(directive.get("end", 0.0)) - float(directive.get("start", 0.0))
            if planned > 0.0:
                desired = planned
        except (TypeError, ValueError):
            pass

        visual_style = str(directive.get("visual_style", "")).strip()
        query = " ".join(part for part in (sentence, visual_style, purpose) if part)
        scene, score = choose_scene(query, scenes, used, desired, role)
        used.append(scene.id)

        available = max(0.4, scene.duration)
        source_duration = min(available, max(0.4, desired))
        source_start = scene.start
        source_end = min(scene.end, source_start + source_duration)
        actual_duration = source_end - source_start
        target_start = cursor
        target_end = cursor + actual_duration

        effects = _directive_effects(directive, role)
        motion = str(directive.get("camera_motion", "")).strip()
        transition = str(directive.get("transition", "")).strip()
        directive_label = " ".join(part for part in (motion, transition) if part)
        label = f"{role.title()} - {sentence[:70]}"
        if directive_label:
            label += f" [{directive_label}]"

        segments.append(
            TimelineSegment(
                id=f"edit_{index:03d}",
                role=role,
                start=round(target_start, 3),
                end=round(target_end, 3),
                source_scene_id=scene.id,
                source_start=round(source_start, 3),
                source_end=round(source_end, 3),
                label=label,
                transcript=sentence,
                caption_emphasis=[w for w in _tokenize(sentence) if len(w) >= 7][:4],
                effects=effects,
                score=score,
            )
        )
        cursor = target_end

    timeline = EditTimeline(
        duration=round(cursor, 3),
        aspect_ratio=aspect_ratio,
        segments=segments,
        source_video=source_video,
    )
    errors = timeline.validate()
    if errors:
        raise ValueError("Invalid edit timeline: " + "; ".join(errors))
    return timeline


def timeline_to_composer_plan(timeline: EditTimeline) -> List[Tuple[float, str]]:
    """Return the legacy composer plan format while preserving new timeline data."""
    return [(round(s.duration, 3), s.label) for s in timeline.segments]


def save_timeline(timeline: EditTimeline, path: str) -> str:
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(timeline.to_dict(), handle, indent=2)
    return path


def load_timeline(path: str) -> EditTimeline:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    segments = [TimelineSegment(**segment) for segment in payload.get("segments", [])]
    return EditTimeline(
        duration=float(payload.get("duration", 0.0)),
        aspect_ratio=str(payload.get("aspect_ratio", "9:16")),
        source_video=payload.get("source_video"),
        music_path=payload.get("music_path"),
        voiceover_path=payload.get("voiceover_path"),
        version=int(payload.get("version", 1)),
        segments=segments,
    )
