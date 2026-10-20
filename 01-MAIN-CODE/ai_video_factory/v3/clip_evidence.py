"""Match planned clip purposes to relevant source-scene evidence.

Importance is never allowed to masquerade as semantic relevance. A scene must
share meaningful evidence with the requested purpose before it can support it.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from ..editorial_jury import score_clip_candidate


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
        ranked: list[dict[str, Any]] = []
        for scene_index, scene in enumerate(scenes, start=1):
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
            # Keep semantic support primary. The editorial jury is a transparent
            # tie-breaker, and it cannot turn an unrelated scene into evidence.
            total = 0.80 * relevance + 0.20 * importance
            judgement = score_clip_candidate(
                scene,
                purpose,
                visual_style=visual,
                source_duration=source_duration,
                topic=str(footage_evidence.get("topic", "")),
                target_emotion=str(footage_evidence.get("target_emotion", "")),
            )
            gated_jury_score = (float(judgement["overall"]) / 100.0) * min(1.0, relevance / 0.30)
            selection_score = 0.85 * relevance + 0.15 * gated_jury_score if relevance > 0 else 0.0
            ranked.append({
                "scene": scene,
                "scene_index": scene_index,
                "relevance": relevance,
                "total": total,
                "selection_score": selection_score,
                "judgement": judgement,
                "lexical": lexical,
                "semantic": semantic,
            })

        ranked.sort(
            key=lambda item: (
                float(item["selection_score"]),
                float(item["relevance"]),
                float(item["total"]),
                float(item["judgement"]["overall"]),
            ),
            reverse=True,
        )
        best = ranked[0] if ranked else None
        best_scene = best["scene"] if best else None
        best_score = float(best["total"]) if best else 0.0
        best_relevance = float(best["relevance"]) if best else 0.0
        best_judgement = best["judgement"] if best else None
        alternative_candidates = [
            {
                "rank": rank,
                "scene_id": str(item["scene"].get("id") or f"scene-{item['scene_index']}"),
                "source_start": float(item["scene"].get("start", 0.0)),
                "source_end": float(item["scene"].get("end", 0.0)),
                "relevance_score": round(min(1.0, max(0.0, float(item["relevance"]))), 3),
                "ranking_score": round(min(1.0, max(0.0, float(item["selection_score"]))), 3),
                "editorial_score": item["judgement"]["overall"],
                "editorial_judgement": item["judgement"],
            }
            for rank, item in enumerate(ranked[:3], start=1)
        ]

        if best_scene is None:
            results.append({
                "clip_index": index,
                "source_asset": source_asset,
                "status": "unsupported",
                "evidence_score": 0.0,
                "relevance_score": 0.0,
                "reason": "no analyzed source scene available",
                "purpose": clip.get("purpose", ""),
                "editorial_judgement": None,
                "candidate_rankings": [],
            })
            continue

        motion = min(1.0, max(0.0, float(best_scene.get("motion_score", 0.0))))
        audio = min(1.0, max(0.0, float(best_scene.get("audio_energy", 0.0))))
        try:
            face_count = max(0, int(best_scene.get("face_count", 0) or 0))
        except (TypeError, ValueError):
            face_count = 0
        visual_evidence = min(1.0, 0.55 * motion + 0.35 * audio + (0.10 if face_count > 0 else 0.0))
        semantic_supported = best_relevance > 0.0 and (
            best_score > 0.10 and best_relevance >= 0.25
        )
        traceable_weak = (
            best_score >= 0.12
            or visual_evidence >= 0.15
        )
        critical_purpose = purpose in {"hook", "payoff", "punchline", "climax", "final impact"}
        status = (
            "supported"
            if semantic_supported and best_score >= 0.30
            else "weak"
            if semantic_supported and traceable_weak
            else "weak"
            if (not critical_purpose) and traceable_weak
            else "unsupported"
        )
        results.append({
            "clip_index": index,
            "source_asset": source_asset,
            "source_scene": str(best_scene.get("id", "")),
            "source_start": float(best_scene.get("start", 0.0)),
            "source_end": float(best_scene.get("end", 0.0)),
            "semantic_tags": sorted(_tokens(
                " ".join(
                    str(best_scene.get(key, ""))
                    for key in ("description", "objects", "text")
                )
            ))[:16],
            "evidence_score": round(min(1.0, best_score), 3),
            "relevance_score": round(min(1.0, max(0.0, best_relevance)), 3),
            "importance_score": round(min(1.0, max(0.0, float(best_scene.get("importance_score", 0.0)))), 3),
            "status": status,
            "purpose": clip.get("purpose", ""),
            "reason": (
                "semantic purpose/style relevance with editorial scoring as a gated tie-breaker"
                if semantic_supported and status == "supported"
                else "source scene is traceable but semantic labels are sparse; marked weak and requires review"
                if status == "weak"
                else "no meaningful semantic or visual evidence matched any analyzed source scene"
            ),
            "editorial_judgement": best_judgement,
            "candidate_rankings": alternative_candidates,
        })
    return results


__all__ = ["build_clip_evidence"]
