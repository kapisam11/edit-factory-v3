"""Match planned clip purposes to relevant source-scene evidence.

Importance is never allowed to masquerade as semantic relevance. A scene must
share meaningful evidence with the requested purpose before it can support it.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence


_PURPOSE_TERMS = {
    "hook": {"hook", "opening", "reveal", "reaction", "result"},
    "setup": {"setup", "context", "start", "begin"},
    "context": {"context", "background", "detail", "scene"},
    "curiosity": {"question", "unknown", "detail", "clue"},
    "question": {"question", "unknown", "clue"},
    "trait": {"character", "behavior", "trait", "action"},
    "evidence": {"evidence", "proof", "detail", "object"},
    "escalation": {"pressure", "action", "change", "intensity", "motion"},
    "conflict": {"conflict", "problem", "threat", "risk"},
    "climax": {"climax", "impact", "action", "peak"},
    "payoff": {"payoff", "answer", "proof", "result", "reveal"},
    "punchline": {"reaction", "joke", "surprise", "result"},
    "reaction": {"reaction", "face", "response"},
    "final impact": {"final", "impact", "reaction", "result"},
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", str(text).lower()))


def _clip_relevance(
    purpose: str,
    visual: str,
    scene_tokens: set[str],
    *,
    scene_start: float,
    scene_end: float,
    source_duration: float,
) -> tuple[float, float, float]:
    clip_tokens = _tokens(f"{purpose} {visual}")
    desired = _PURPOSE_TERMS.get(purpose, _tokens(purpose))
    lexical = len(clip_tokens & scene_tokens) / max(1, len(clip_tokens))
    semantic = len(desired & scene_tokens) / max(1, len(desired))
    middle = max(0.0, (scene_start + scene_end) / 2.0)
    normalized = middle / max(0.001, source_duration)
    temporal_role = 0.0
    if purpose == "hook":
        temporal_role = max(0.0, 1.0 - normalized * 4.0)
    elif purpose in {"payoff", "punchline", "climax", "final impact"}:
        temporal_role = max(0.0, (normalized - 0.55) / 0.45)
    elif purpose in {"escalation", "conflict", "threat"}:
        temporal_role = min(1.0, 0.5 + abs(normalized - 0.5))
    role_weight = 0.50 if purpose in {"hook", "payoff", "punchline", "climax", "final impact"} else 0.20
    semantic_base = 0.50 * lexical + 0.30 * semantic
    # Source position is secondary context only. It is explicitly zeroed when
    # no lexical/semantic evidence exists, so timing can never manufacture support.
    role_gate = min(1.0, semantic_base * 2.0)
    role_support = role_weight * temporal_role * role_gate
    relevance = semantic_base + role_support
    return lexical, semantic, relevance


def build_clip_evidence(
    clip_plan: Sequence[Mapping[str, Any]],
    footage_evidence: Mapping[str, Any],
    *,
    source_asset: str,
) -> list[dict[str, Any]]:
    scenes = [
        item for item in (footage_evidence.get("top_scenes") or [])
        if isinstance(item, Mapping)
    ]
    try:
        source_duration = max(
            float(item.get("end", 0.0))
            for item in scenes
            if float(item.get("end", 0.0)) > 0
        )
    except ValueError:
        source_duration = 1.0
    results: list[dict[str, Any]] = []
    for index, clip in enumerate(clip_plan, start=1):
        purpose = str(clip.get("purpose", "")).strip().lower()
        visual = str(clip.get("visual_style", "")).strip()
        best: Mapping[str, Any] | None = None
        best_score = 0.0
        best_relevance = 0.0
        for scene in scenes:
            scene_tokens = _tokens(
                " ".join(
                    str(scene.get(key, ""))
                    for key in ("description", "transcript", "objects", "text")
                )
            )
            lexical, semantic, relevance = _clip_relevance(
                purpose,
                visual,
                scene_tokens,
                scene_start=float(scene.get("start", 0.0)),
                scene_end=float(scene.get("end", 0.0)),
                source_duration=source_duration,
            )
            importance = min(1.0, max(0.0, float(scene.get("importance_score", 0.0))))
            # Importance can break ties, but can never create relevance by itself.
            total = 0.80 * relevance + 0.20 * importance
            if relevance > best_relevance or (
                abs(relevance - best_relevance) <= 1e-9 and total > best_score
            ):
                best_score = total
                best_relevance = relevance
                best = scene

        if best is None:
            results.append({
                "clip_index": index,
                "source_asset": source_asset,
                "status": "unsupported",
                "evidence_score": 0.0,
                "relevance_score": 0.0,
                "reason": "no analyzed source scene available",
                "purpose": clip.get("purpose", ""),
            })
            continue

        motion = min(1.0, max(0.0, float(best.get("motion_score", 0.0))))
        audio = min(1.0, max(0.0, float(best.get("audio_energy", 0.0))))
        face_count = max(0, int(best.get("face_count", 0) or 0))
        visual_evidence = min(1.0, 0.55 * motion + 0.35 * audio + (0.10 if face_count > 0 else 0.0))
        semantic_supported = best_relevance > 0.0 and (
            best_score > 0.10 and best_relevance >= 0.25
        )
        traceable_weak = (
            best_score >= 0.12
            or visual_evidence >= 0.15
        )
        status = (
            "supported"
            if semantic_supported and best_score >= 0.30
            else "weak"
            if traceable_weak
            else "unsupported"
        )
        results.append({
            "clip_index": index,
            "source_asset": source_asset,
            "source_scene": str(best.get("id", "")),
            "source_start": float(best.get("start", 0.0)),
            "source_end": float(best.get("end", 0.0)),
            "semantic_tags": sorted(_tokens(
                " ".join(
                    str(best.get(key, ""))
                    for key in ("description", "objects", "text")
                )
            ))[:16],
            "evidence_score": round(min(1.0, best_score), 3),
            "relevance_score": round(min(1.0, best_relevance), 3),
            "importance_score": round(min(1.0, max(0.0, float(best.get("importance_score", 0.0)))), 3),
            "status": status,
            "purpose": clip.get("purpose", ""),
            "reason": (
                "semantic purpose/style relevance with importance as secondary context"
                if status != "unsupported"
                else "no meaningful semantic or temporal-role evidence matched any analyzed source scene"
            ),
        })
    return results


__all__ = ["build_clip_evidence"]
