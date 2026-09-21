"""Convert a short-form script and V3 blueprint into a footage-aware edit timeline."""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .production_models import EditTimeline, Scene, TimelineSegment
from .scene_intelligence import score_scene
from .validation import validate_target_seconds
from .v3_semantics import semantic_similarity

ROLE_EFFECTS = {
    "hook": ["impact", "quick_zoom"],
    "intro": ["subtle_zoom"],
    "conflict": ["camera move"],
    "climax": ["speed ramp", "impact"],
    "payoff": ["slow push", "fade"],
}

V3_PURPOSE_ROLE = {
    "Hook": "hook",
    "Threat": "conflict",
    "Escalation": "climax",
    "Climax": "climax",
    "Payoff": "payoff",
    "Punchline": "payoff",
    "Final impact": "payoff",
    "Memory": "intro",
    "Tribute": "payoff",
}


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", str(text).lower())


def _overlap_score(a: str, b: str) -> float:
    """Measure deterministic lexical evidence symmetrically using token-set F1."""
    aa = set(_tokenize(a))
    bb = set(_tokenize(b))
    if not aa or not bb:
        return 0.0
    overlap = len(aa & bb)
    precision = overlap / max(1, len(bb))
    recall = overlap / max(1, len(aa))
    return 2.0 * precision * recall / max(1e-9, precision + recall)


def _sentences(script: str) -> List[str]:
    lines = [line.strip() for line in str(script).splitlines() if line.strip()]
    return lines or [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(script)) if s.strip()]


def _fit_script_to_beats(script: str, count: int) -> List[str]:
    """Map model prose onto the immutable V3 beat count without changing beat boundaries."""
    if count <= 0:
        return []
    words = str(script).split()
    if not words:
        raise ValueError("Script is empty")
    chunks: List[str] = []
    for i in range(count):
        start = round(i * len(words) / count)
        end = round((i + 1) * len(words) / count)
        chunk = " ".join(words[start:end]).strip()
        chunks.append(chunk or words[min(i, len(words) - 1)])
    return chunks


def _role_for_index(index: int, count: int) -> str:
    if count <= 1:
        return "hook"
    ratio = index / max(1, count - 1)
    if index == 0:
        return "hook"
    if ratio >= 0.88:
        return "payoff"
    if ratio >= 0.65:
        return "climax"
    if ratio >= 0.4:
        return "conflict"
    return "intro"


def _desired_duration(total: float, index: int, count: int) -> float:
    weights = [1.0 for _ in range(count)]
    if count >= 5:
        weights[0] = 0.8
        weights[-1] = 1.15
        for i in range(1, count - 1):
            if _role_for_index(i, count) == "climax":
                weights[i] = 1.25
    return max(1.0, total * weights[index] / (sum(weights) or 1.0))


def _has_semantic_evidence(scene: Scene) -> bool:
    """Return whether a scene contains non-generic text that can support relevance matching."""
    transcript = str(scene.transcript or "").strip()
    objects = " ".join(str(item).strip() for item in scene.objects if str(item).strip())
    ocr_text = " ".join(str(item).strip() for item in scene.text if str(item).strip())
    description = str(scene.description or "").strip()
    generic_prefix = "source footage from "
    if description.lower().startswith(generic_prefix):
        description = ""
    ocr_tokens = _tokenize(ocr_text)
    substantive_ocr = len(ocr_tokens) >= 5
    return bool(" ".join(part for part in (description, transcript, objects) if part).strip()) or substantive_ocr


def choose_scene(
    query: str,
    scenes: Sequence[Scene],
    used_ids: Iterable[str],
    desired_seconds: float,
    role: str,
    *,
    min_match_score: float = 0.15,
    allow_reuse: bool = True,
) -> Tuple[Scene, float]:
    """Rank footage and optionally reject repeated scenes when unique coverage is required."""
    used = set(used_ids)
    available = [scene for scene in scenes if scene.id not in used]
    if not available:
        if not allow_reuse:
            raise ValueError("No unused scenes remain; V3 refuses to repeat footage to fill a beat")
        available = list(scenes)
    if not available:
        raise ValueError("No scenes are available for planning")

    ranked: List[Tuple[float, Scene]] = []
    semantic_evidence_available = any(_has_semantic_evidence(scene) for scene in available)
    for scene in available:
        lexical = _overlap_score(query, scene.searchable_text)
        query_tokens = set(_tokenize(query))
        scene_tokens = set(_tokenize(scene.searchable_text))
        lexical_evidence = (
            len(query_tokens & scene_tokens) / max(1, len(query_tokens))
            if query_tokens
            else 0.0
        )
        semantic = semantic_similarity(query, scene.searchable_text)
        relevance = 0.45 * lexical + 0.55 * semantic
        # Explicit transcript/OCR keyword evidence is valid scene relevance.
        # The hard gate applies to every candidate before ranking so a highly
        # salient but irrelevant scene can never bypass the relevance contract.
        hard_relevance = max(relevance, lexical_evidence)
        if semantic_evidence_available and hard_relevance < min_match_score:
            continue
        salience = score_scene(scene, query)
        duration_fit = min(scene.duration, desired_seconds) / max(scene.duration, desired_seconds, 0.01)
        role_bonus = 0.0
        if role == "climax":
            role_bonus = 0.20 * scene.motion_score + 0.20 * scene.importance_score
        elif role == "payoff":
            role_bonus = 0.10 * (1.0 - scene.motion_score) + 0.15 * scene.face_count / max(1, scene.face_count + 1)
        elif role == "hook":
            role_bonus = 0.20 * scene.importance_score
        freshness = 0.10
        value = 0.20 * lexical + 0.25 * semantic + 0.25 * salience + 0.15 * duration_fit + role_bonus + freshness
        ranked.append((value, scene))
    if not ranked:
        if semantic_evidence_available:
            raise ValueError(
                f"No scene meets minimum relevance confidence "
                f"({min_match_score:.3f}) for query: {query[:120]}"
            )
        raise ValueError(f"No eligible scene is available for query: {query[:120]}")
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best_scene = ranked[0]
    return best_scene, round(float(best_score), 4)


def _directive_for_index(directives: Mapping[str, Any], index: int) -> Mapping[str, Any]:
    clip_plan = directives.get("clip_plan", [])
    if isinstance(clip_plan, list) and index < len(clip_plan) and isinstance(clip_plan[index], Mapping):
        return clip_plan[index]
    return {}


def _adaptive_motion_transition(scene: Scene, role: str, purpose: str, requested_motion: str, requested_transition: str) -> Tuple[str, str]:
    """Choose renderable motion/transition from the selected shot's characteristics."""
    if scene.motion_score >= 0.72:
        motion = "tracking" if role in {"conflict", "climax"} else "reframe"
    elif scene.face_count > 0 and role in {"hook", "payoff"}:
        motion = "punch-in" if role == "hook" else "slow push"
    elif scene.importance_score >= 0.72:
        motion = "micro-zoom"
    else:
        motion = "subtle-parallax"
    if purpose in {"Climax", "Escalation", "Punchline"} and scene.motion_score >= 0.55:
        transition = "speed ramp"
    elif scene.motion_score < 0.25 and purpose in {"Memory", "Tribute", "Final impact", "Payoff"}:
        transition = "dissolve"
    elif purpose in {"Hook", "Threat", "Question"}:
        transition = "hard cut"
    else:
        transition = "match cut"
    return motion, transition


def _directive_effects(directive: Mapping[str, Any], role: str, scene: Optional[Scene] = None) -> List[str]:
    effects = list(ROLE_EFFECTS.get(role, []))
    requested_motion = str(directive.get("camera_motion", ""))
    requested_transition = str(directive.get("transition", ""))
    purpose = str(directive.get("purpose", ""))
    motion, transition = _adaptive_motion_transition(scene, role, purpose, requested_motion, requested_transition) if scene is not None else (requested_motion.lower(), requested_transition.lower())
    if motion in {"punch-in", "micro-zoom"}:
        effects.append("quick_zoom")
    elif motion in {"tracking", "reframe", "slow push"}:
        effects.append("camera move")
    elif motion == "subtle-parallax":
        effects.append("soft settle")
    if transition in {"dissolve", "match cut", "j-cut"}:
        effects.append("cinematic transition")
    elif transition == "speed ramp":
        effects.append("speed ramp")
    return list(dict.fromkeys(effects))


def _retention_effects_for_window(retention_map: Sequence[Mapping[str, Any]], start: float, end: float) -> Tuple[List[str], List[str]]:
    effects: List[str] = []
    labels: List[str] = []
    for event in retention_map:
        if not isinstance(event, Mapping):
            continue
        try:
            timestamp = float(event.get("time", -1.0))
        except (TypeError, ValueError):
            continue
        if not start - 1e-6 <= timestamp < end - 1e-6:
            continue
        kind = str(event.get("kind", "motion")).strip().lower()
        labels.append(f"retention:{kind}")
        if kind == "zoom":
            effects.append("quick_zoom")
        elif kind in {"motion", "angle", "clip"}:
            effects.append("camera move")
        elif kind == "beat drop":
            effects.append("speed ramp")
        elif kind == "text":
            effects.append("retention accent")
    return list(dict.fromkeys(effects)), labels


def _validate_v3_target_seconds(value: float, field_name: str = "total_seconds", platform_profile: Optional[Mapping[str, Any]] = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not math.isfinite(result) or not 8.0 <= result <= 180.0:
        raise ValueError(f"{field_name} must be between 8 and 180 seconds")
    if platform_profile:
        maximum = platform_profile.get("max_seconds")
        if maximum is not None and result > float(maximum):
            raise ValueError(f"{field_name} exceeds the selected platform limit of {maximum:g} seconds")
    return result


def build_timeline(script: str, scenes: Sequence[Scene], *, total_seconds: Optional[float] = None, aspect_ratio: str = "9:16", source_video: Optional[str] = None, creative_directives: Optional[Mapping[str, Any]] = None) -> EditTimeline:
    """Build a timeline whose beat structure is controlled by the V3 blueprint when provided."""
    if not str(script).strip():
        raise ValueError("Script is empty")
    if not scenes:
        raise ValueError("Scene index is empty")
    directives = creative_directives if isinstance(creative_directives, Mapping) else {}
    clip_plan = directives.get("clip_plan") if isinstance(directives.get("clip_plan"), list) else []
    retention_map = directives.get("retention_map") if isinstance(directives.get("retention_map"), list) else []
    if clip_plan:
        target = _validate_v3_target_seconds(total_seconds if total_seconds is not None else clip_plan[-1].get("end", 30.0), "total_seconds", directives.get("platform_profile") if isinstance(directives.get("platform_profile"), Mapping) else None)
        lines = _fit_script_to_beats(script, len(clip_plan))
    else:
        lines = _sentences(script)
        target = validate_target_seconds(sum(s.duration for s in scenes[: max(1, len(lines))]), "total_seconds") if total_seconds is None else validate_target_seconds(total_seconds, "total_seconds")

    cursor = 0.0
    used: List[str] = []
    segments: List[TimelineSegment] = []
    min_match = float(directives.get("min_scene_match_score", 0.15) or 0.15)
    if not 0.0 < min_match <= 1.0:
        raise ValueError("min_scene_match_score must be between 0 and 1")

    for index, sentence in enumerate(lines):
        directive = _directive_for_index(directives, index)
        purpose = str(directive.get("purpose", "")).strip()
        role = V3_PURPOSE_ROLE.get(purpose) or _role_for_index(index, len(lines))
        desired = _desired_duration(target, index, len(lines))
        planned_start: Optional[float] = None
        planned_end: Optional[float] = None
        if clip_plan:
            try:
                planned_start = float(directive["start"])
                planned_end = float(directive["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"V3 clip {index + 1} has invalid start/end") from exc
            if planned_start < -0.001 or planned_end <= planned_start:
                raise ValueError(f"V3 clip {index + 1} has invalid interval")
            if abs(planned_start - cursor) > 0.01:
                raise ValueError(f"V3 clip {index + 1} does not start at the previous blueprint boundary")
            desired = planned_end - planned_start
        else:
            try:
                planned = float(directive.get("end", 0.0)) - float(directive.get("start", 0.0))
                if planned > 0.0:
                    desired = planned
            except (TypeError, ValueError):
                pass

        query = " ".join(part for part in (sentence, str(directive.get("visual_style", "")), purpose) if part)
        scene, score = choose_scene(query, scenes, used, desired, role, min_match_score=min_match, allow_reuse=not bool(clip_plan))
        used.append(scene.id)
        source_duration = min(max(0.4, scene.duration), max(0.4, desired))
        source_start = scene.start
        source_end = min(scene.end, source_start + source_duration)
        target_start = planned_start if planned_start is not None else cursor
        target_end = planned_end if planned_end is not None else cursor + (source_end - source_start)
        effects = _directive_effects(directive, role, scene)
        retention_effects, retention_labels = _retention_effects_for_window(retention_map, target_start, target_end)
        effects = list(dict.fromkeys(effects + retention_effects))
        effective_motion, effective_transition = _adaptive_motion_transition(scene, role, purpose, str(directive.get("camera_motion", "")), str(directive.get("transition", "")))
        directive_label = " ".join(part for part in (effective_motion, effective_transition, *retention_labels) if part)
        label = f"{role.title()} - {sentence[:70]}"
        if directive_label:
            label += f" [{directive_label}]"
        segments.append(TimelineSegment(
            id=f"edit_{index:03d}", role=role, start=round(target_start, 3), end=round(target_end, 3),
            source_scene_id=scene.id, source_start=round(source_start, 3), source_end=round(source_end, 3),
            label=label, transcript=sentence,
            caption_emphasis=[w for w in _tokenize(sentence) if len(w) >= 7][:4], effects=effects, score=score,
        ))
        cursor = target_end

    if clip_plan and abs(cursor - target) > 0.01:
        raise ValueError(f"V3 blueprint duration mismatch: {cursor:.3f} != {target:.3f}")
    timeline = EditTimeline(duration=round(cursor, 3), aspect_ratio=aspect_ratio, segments=segments, source_video=source_video)
    errors = timeline.validate()
    if errors:
        raise ValueError("Invalid edit timeline: " + "; ".join(errors))
    return timeline


def timeline_to_composer_plan(timeline: EditTimeline) -> List[Tuple[float, str]]:
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
    return EditTimeline(duration=float(payload.get("duration", 0.0)), aspect_ratio=str(payload.get("aspect_ratio", "9:16")), source_video=payload.get("source_video"), music_path=payload.get("music_path"), voiceover_path=payload.get("voiceover_path"), version=int(payload.get("version", 1)), segments=segments)