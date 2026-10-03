"""Match planned clip purposes to actual source-scene evidence."""
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
    results: list[dict[str, Any]] = []
    for index, clip in enumerate(clip_plan, start=1):
        purpose = str(clip.get("purpose", "")).strip().lower()
        visual = str(clip.get("visual_style", "")).strip()
        desired = _PURPOSE_TERMS.get(purpose, _tokens(purpose))
        best = None
        best_score = 0.0
        clip_tokens = _tokens(f"{purpose} {visual}")
        for scene in scenes:
            scene_tokens = _tokens(
                " ".join(
                    str(scene.get(key, ""))
                    for key in ("description", "transcript", "objects", "text")
                )
            )
            overlap = len(clip_tokens & scene_tokens) / max(1, len(clip_tokens))
            semantic = len(desired & scene_tokens) / max(1, len(desired))
            score = 0.55 * overlap + 0.45 * semantic
            score += 0.20 * float(scene.get("importance_score", 0.0))
            if score > best_score:
                best_score = score
                best = scene
        if best is None:
            results.append({
                "clip_index": index,
                "source_asset": source_asset,
                "status": "unsupported",
                "evidence_score": 0.0,
                "reason": "no analyzed source scene available",
            })
            continue
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
            "status": "supported" if best_score >= 0.15 else "weak",
            "purpose": clip.get("purpose", ""),
        })
    return results


__all__ = ["build_clip_evidence"]
