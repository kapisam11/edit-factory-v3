"""Explainable editorial scoring for source-scene candidates.

Scores are deterministic heuristics, not learned predictions of audience behavior.
Every result includes its method/version and reasons so the dashboard can explain
why a scene appears higher or lower in a review list.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping


JURY_VERSION = "1.0.0"
_PURPOSE_TERMS: dict[str, set[str]] = {
    "hook": {"hook", "opening", "reveal", "reaction", "result", "surprise", "why", "what"},
    "setup": {"setup", "context", "start", "begin", "before", "who", "where"},
    "context": {"context", "background", "detail", "scene", "because", "explains"},
    "curiosity": {"question", "unknown", "detail", "clue", "secret", "why", "what"},
    "question": {"question", "unknown", "clue", "why", "what", "how"},
    "trait": {"character", "behavior", "trait", "action", "habit"},
    "evidence": {"evidence", "proof", "detail", "object", "shows", "reveals"},
    "escalation": {"pressure", "action", "change", "intensity", "motion", "worse"},
    "conflict": {"conflict", "problem", "threat", "risk", "fight", "loss"},
    "climax": {"climax", "impact", "action", "peak", "result", "finally"},
    "payoff": {"payoff", "answer", "proof", "result", "reveal", "finally"},
    "punchline": {"reaction", "joke", "surprise", "result", "funny"},
    "reaction": {"reaction", "face", "response", "laugh", "shock"},
    "final impact": {"final", "impact", "reaction", "result", "ending"},
}
_CURIOSITY_TERMS = {"why", "what", "how", "secret", "finally", "revealed", "unexpected", "never", "until", "but"}
_DANGLING_OPENERS = {"he", "she", "they", "it", "this", "that", "there", "because", "and", "but", "so", "then"}
_WORD_RE = re.compile(r"[a-z0-9']+")


def _tokens(value: Any) -> list[str]:
    return _WORD_RE.findall(str(value or "").lower())


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _number(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _finite(value, default)))


def _score(value: float) -> float:
    return round(max(0.0, min(100.0, value * 100.0)), 1)


def score_clip_candidate(
    scene: Mapping[str, Any],
    purpose: str,
    *,
    visual_style: str = "",
    source_duration: float | None = None,
    topic: str = "",
    target_emotion: str = "",
) -> dict[str, Any]:
    """Score a scene across separate editorial dimensions with explicit reasoning."""
    purpose_key = str(purpose or "context").strip().lower()
    description = str(scene.get("description") or "")
    transcript = str(scene.get("transcript") or "")
    objects = scene.get("objects") or ""
    on_screen_text = scene.get("text") or ""
    combined_text = " ".join((description, transcript, str(objects), str(on_screen_text)))
    words = _tokens(combined_text)
    word_set = set(words)
    purpose_terms = _PURPOSE_TERMS.get(purpose_key, set(_tokens(purpose_key)))
    topic_terms = set(_tokens(f"{topic} {target_emotion} {visual_style}"))
    transcript_words = _tokens(transcript)
    transcript_set = set(transcript_words)

    importance = _number(scene.get("importance_score"))
    motion = _number(scene.get("motion_score"))
    audio = _number(scene.get("audio_energy"))
    try:
        face_count = max(0, int(scene.get("face_count") or 0))
    except (TypeError, ValueError, OverflowError):
        face_count = 0
    face_signal = 1.0 if face_count else 0.0

    purpose_overlap = len(word_set & purpose_terms) / max(1, len(purpose_terms))
    context_overlap = len(word_set & topic_terms) / max(1, len(topic_terms)) if topic_terms else 0.0
    source_support = min(1.0, 0.55 * purpose_overlap + 0.30 * context_overlap + 0.15 * importance)

    opener = transcript_words[0] if transcript_words else ""
    sentence_complete = bool(re.search(r"[.!?][\"')\]]?\s*$", transcript.strip()))
    context_independence = 0.38
    if len(transcript_words) >= 7:
        context_independence += 0.22
    if opener and opener not in _DANGLING_OPENERS:
        context_independence += 0.12
    if sentence_complete:
        context_independence += 0.12
    if len(transcript_words) >= 4 and not ({"he", "she", "they", "it"} >= set(transcript_words[:2])):
        context_independence += 0.08
    context_independence = min(1.0, context_independence)

    curiosity = min(1.0, len(set(transcript_words) & _CURIOSITY_TERMS) * 0.22)
    hook_strength = min(1.0, 0.42 * importance + 0.25 * motion + 0.18 * audio + 0.15 * curiosity)
    narrative_fit = min(1.0, 0.65 * purpose_overlap + 0.20 * context_overlap + 0.15 * importance)

    start = max(0.0, _finite(scene.get("start", 0.0)))
    end = max(start, _finite(scene.get("end", start), start))
    duration = max(0.0, end - start)
    if duration <= 0:
        pacing = 0.25
    elif duration < 0.75:
        pacing = max(0.25, duration / 0.75)
    elif duration <= 7.0:
        pacing = 1.0
    else:
        pacing = max(0.30, 1.0 - min(0.70, (duration - 7.0) / 28.0))
    pacing = min(1.0, 0.72 * pacing + 0.18 * motion + 0.10 * audio)

    unique_ratio = len(set(transcript_words)) / max(1, len(transcript_words))
    words_per_second = len(transcript_words) / max(1.0, duration)
    information_density = min(1.0, 0.55 * unique_ratio + 0.45 * min(1.0, words_per_second / 3.5))
    visual_energy = min(1.0, 0.55 * motion + 0.30 * audio + 0.15 * face_signal)

    weights = {
        "hook": 0.18,
        "context_independence": 0.12,
        "narrative_fit": 0.22,
        "pacing": 0.12,
        "information_density": 0.12,
        "visual_energy": 0.10,
        "source_support": 0.14,
    }
    if purpose_key == "hook":
        weights.update({
            "hook": 0.27,
            "context_independence": 0.12,
            "narrative_fit": 0.18,
            "pacing": 0.11,
            "information_density": 0.10,
            "visual_energy": 0.08,
            "source_support": 0.14,
        })
    elif purpose_key in {"payoff", "climax", "punchline", "final impact"}:
        weights.update({
            "hook": 0.14,
            "context_independence": 0.15,
            "narrative_fit": 0.27,
            "pacing": 0.12,
            "information_density": 0.12,
            "visual_energy": 0.06,
            "source_support": 0.14,
        })
    # Purpose-specific weights must remain a convex combination. Without this
    # normalization the overall score could exceed 100 and saturate at the cap.
    weight_sum = sum(weights.values())
    weights = {name: value / weight_sum for name, value in weights.items()}
    overall = sum({
        "hook": hook_strength,
        "context_independence": context_independence,
        "narrative_fit": narrative_fit,
        "pacing": pacing,
        "information_density": information_density,
        "visual_energy": visual_energy,
        "source_support": source_support,
    }[name] * weight for name, weight in weights.items())

    dimensions = {
        "hook_strength": _score(hook_strength),
        "context_independence": _score(context_independence),
        "narrative_fit": _score(narrative_fit),
        "pacing": _score(pacing),
        "information_density": _score(information_density),
        "visual_energy": _score(visual_energy),
        "source_support": _score(source_support),
    }
    strongest = sorted(dimensions.items(), key=lambda item: item[1], reverse=True)[:2]
    weakest = sorted(dimensions.items(), key=lambda item: item[1])[:2]
    reasons = [
        f"strongest: {name.replace('_', ' ')} ({value:.1f}/100)"
        for name, value in strongest
    ]
    reasons.extend(
        f"review: {name.replace('_', ' ')} ({value:.1f}/100)"
        for name, value in weakest
        if value < 55.0
    )
    if source_support < 0.20:
        reasons.append("weak semantic match: inspect the source scene before using it")
    if not transcript.strip():
        reasons.append("no transcript evidence: text-based judgments have low confidence")

    return {
        "overall": _score(overall),
        "dimensions": dimensions,
        "weights": {name: round(weight, 4) for name, weight in weights.items()},
        "reasons": reasons,
        "purpose": purpose_key,
        "duration_seconds": round(duration, 3),
        "evaluator": "deterministic_editorial_heuristic",
        "version": JURY_VERSION,
        "is_audience_prediction": False,
        "confidence": round(min(1.0, 0.25 + 0.60 * source_support + 0.15 * bool(transcript.strip())), 3),
    }


def rank_clip_candidates(
    scenes: list[Mapping[str, Any]],
    purpose: str,
    *,
    visual_style: str = "",
    source_duration: float | None = None,
    topic: str = "",
    target_emotion: str = "",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return a short, explainable ranked list without discarding weak candidates."""
    ranked = []
    for index, scene in enumerate(scenes):
        judgement = score_clip_candidate(
            scene,
            purpose,
            visual_style=visual_style,
            source_duration=source_duration,
            topic=topic,
            target_emotion=target_emotion,
        )
        start = round(max(0.0, _finite(scene.get("start", 0.0))), 3)
        end = round(max(start, _finite(scene.get("end", start), start)), 3)
        ranked.append({
            "rank": 0,
            "scene_id": str(scene.get("id") or f"scene-{index + 1}"),
            "start": start,
            "end": end,
            "editorial_score": judgement["overall"],
            "judgement": judgement,
        })
    ranked.sort(key=lambda item: (item["editorial_score"], item["judgement"]["confidence"]), reverse=True)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked[:max(1, min(int(limit), 20))]


__all__ = ["JURY_VERSION", "rank_clip_candidates", "score_clip_candidate"]
