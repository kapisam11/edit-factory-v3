"""Hook planning module: generate candidates, then evaluate them against evidence."""
from __future__ import annotations

from typing import Any, Mapping

from .audience import parse_audience
from .hook_eval import evaluate_hook_candidates, generate_hook_candidates


def generate_hooks(core: Any, edit_type: Any, *, source_evidence: Mapping[str, Any] | None = None) -> list[Any]:
    from ..v3_engine import HookPack
    evaluated = evaluate_hook_candidates(
        generate_hook_candidates(
            core.topic,
            core.stakes,
            core.emotional_angle,
            core.watch_to_end_reason,
            edit_type.value,
            source_evidence=source_evidence,
        ),
        core.topic,
        core.stakes,
        core.emotional_angle,
        core.watch_to_end_reason,
        source_evidence=source_evidence,
        audience=parse_audience(
            str((source_evidence or {}).get("audience_label", ""))
        ).to_dict(),
    )
    return [
        HookPack(
            candidate.visual,
            candidate.text,
            candidate.emotional_reason,
            evaluation.score,
            evaluation=evaluation.to_dict(),
            score_semantics="editorial_heuristic_score",
        )
        for candidate, evaluation in evaluated
    ]


__all__ = ["generate_hooks"]
