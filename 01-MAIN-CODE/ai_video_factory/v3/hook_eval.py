"""Candidate generation and source-evidence-aware hook evaluation.

Hook text is generated as a candidate first, then scored using explicit evidence.
When source evidence is unavailable the evaluator remains deterministic, but its
output is explicitly labelled as a heuristic rather than a learned confidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class HookCandidate:
    visual: str
    text: str
    emotional_reason: str
    generation_method: str = "template_candidate"


@dataclass(frozen=True)
class HookEvaluation:
    clarity: float
    curiosity: float
    specificity: float
    emotional_relevance: float
    novelty: float
    source_evidence: float
    score: float
    reasons: tuple[str, ...]
    evaluator: str = "deterministic_source_evidence_heuristic"
    version: str = "2.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "clarity": self.clarity,
            "curiosity": self.curiosity,
            "specificity": self.specificity,
            "emotional_relevance": self.emotional_relevance,
            "novelty": self.novelty,
            "source_evidence": self.source_evidence,
            "score": self.score,
            "reasons": list(self.reasons),
            "evaluator": self.evaluator,
            "version": self.version,
        }


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", str(text).lower()))


def _scene_text(scene: Mapping[str, Any]) -> str:
    return " ".join(
        str(scene.get(key, ""))
        for key in ("description", "transcript", "objects", "text")
    )


def _source_evidence_score(candidate: HookCandidate, scenes: Sequence[Mapping[str, Any]]) -> float:
    if not scenes:
        return 0.0
    candidate_tokens = _tokens(candidate.text)
    if not candidate_tokens:
        return 0.0
    best = 0.0
    for scene in scenes:
        scene_tokens = _tokens(_scene_text(scene))
        lexical = len(candidate_tokens & scene_tokens) / max(1, len(candidate_tokens))
        importance = min(1.0, max(0.0, float(scene.get("importance_score", 0.0))))
        motion = min(1.0, max(0.0, float(scene.get("motion_score", 0.0))))
        audio = min(1.0, max(0.0, float(scene.get("audio_energy", 0.0))))
        best = max(best, 0.65 * lexical + 0.20 * importance + 0.075 * motion + 0.075 * audio)
    return round(best, 3)


def generate_hook_candidates(
    topic: str,
    stakes: str,
    angle: str,
    watch_to_end_reason: str,
    edit_type: str,
    *,
    source_evidence: Mapping[str, Any] | None = None,
) -> tuple[HookCandidate, ...]:
    topic_text = " ".join(str(topic).split()[:7])
    style = str(edit_type).lower()
    scenes = [
        item for item in (source_evidence or {}).get("top_scenes", [])
        if isinstance(item, Mapping)
    ]
    source_phrase = ""
    source_transcript = ""
    if scenes:
        strongest = scenes[0]
        source_phrase = " ".join(str(strongest.get("description") or "").split()[:8])
        source_transcript = " ".join(str(strongest.get("transcript") or "").split()[:8])
    evidence_candidates = (
        (
            HookCandidate(
                "Open directly on the most important analyzed source scene.",
                f"The moment {source_phrase or topic_text} became clear",
                "source_scene_importance",
                "source_evidence_candidate",
            ),
            HookCandidate(
                "Open on the strongest analyzed spoken detail before context.",
                f"What the footage shows about {source_transcript or topic_text}",
                "source_transcript",
                "source_evidence_candidate",
            ),
        )
        if scenes
        else ()
    )
    return evidence_candidates + (
        HookCandidate(
            f"Open on the strongest {style} frame before context.",
            f"The moment {topic_text} changed",
            stakes,
        ),
        HookCandidate(
            "Start with the clearest consequence before explanation.",
            f"This changed {topic_text}",
            angle,
        ),
        HookCandidate(
            "Open on the detail that creates an information gap.",
            f"What they missed about {topic_text}",
            watch_to_end_reason,
        ),
        HookCandidate(
            "Open on the strongest evidence and delay the explanation.",
            f"Here is what happened with {topic_text}",
            stakes,
        ),
        HookCandidate(
            "Open on the reaction or result, then backfill context.",
            f"Nobody expected this from {topic_text}",
            angle,
        ),
    )


def evaluate_hook_candidates(
    candidates: Sequence[HookCandidate],
    topic: str,
    stakes: str,
    angle: str,
    watch_to_end_reason: str,
    *,
    source_evidence: Mapping[str, Any] | None = None,
    audience: Mapping[str, Any] | None = None,
) -> tuple[tuple[HookCandidate, HookEvaluation], ...]:
    topic_tokens = _tokens(topic)
    emotional_tokens = _tokens(f"{stakes} {angle} {watch_to_end_reason}")
    texts = [candidate.text for candidate in candidates]
    scenes = [
        item for item in (source_evidence or {}).get("top_scenes", [])
        if isinstance(item, Mapping)
    ]
    audience_text = " ".join(
        str((audience or {}).get(key, ""))
        for key in ("interests", "tone", "hook", "caption_style")
    )
    audience_tokens = _tokens(audience_text)
    outputs: list[tuple[HookCandidate, HookEvaluation]] = []
    for candidate in candidates:
        words = _tokens(candidate.text)
        word_count = len(candidate.text.split())
        clarity = max(0.0, min(1.0, 1.0 - abs(word_count - 6) * 0.08))
        curiosity = 1.0 if any(
            marker in candidate.text.lower()
            for marker in ("what", "why", "how", "missed", "nobody", "moment")
        ) else 0.55
        overlap = len(words & topic_tokens) / max(1, len(words & topic_tokens) + 2)
        specificity = min(1.0, 0.45 + overlap)
        emotional_relevance = min(
            1.0,
            0.40 + 0.60 * len(words & emotional_tokens) / max(1, len(words)),
        )
        if audience_tokens:
            emotional_relevance = min(
                1.0,
                emotional_relevance + 0.05 * len(words & audience_tokens) / max(1, len(words)),
            )
        similarities = []
        for other in texts:
            if other == candidate.text:
                continue
            other_tokens = _tokens(other)
            similarities.append(len(words & other_tokens) / max(1, len(words | other_tokens)))
        novelty = max(0.0, 1.0 - max(similarities, default=0.0))
        source_score = _source_evidence_score(candidate, scenes)
        score = (
            0.16 * clarity
            + 0.20 * curiosity
            + 0.18 * specificity
            + 0.18 * emotional_relevance
            + 0.10 * novelty
            + 0.18 * source_score
        )
        reasons = tuple(
            reason
            for reason, passed in (
                ("clear opening language", clarity >= 0.75),
                ("creates an information gap", curiosity >= 0.80),
                ("mentions the requested topic", bool(words & topic_tokens)),
                ("aligns with stated stakes", bool(words & emotional_tokens)),
                ("supported by source evidence", source_score >= 0.30),
                ("differs from alternative hooks", novelty >= 0.55),
            )
            if passed
        )
        outputs.append(
            (
                candidate,
                HookEvaluation(
                    round(clarity, 3),
                    round(curiosity, 3),
                    round(specificity, 3),
                    round(emotional_relevance, 3),
                    round(novelty, 3),
                    source_score,
                    round(score, 3),
                    reasons,
                ),
            )
        )
    return tuple(sorted(outputs, key=lambda pair: (pair[1].score, pair[1].source_evidence), reverse=True))


__all__ = [
    "HookCandidate",
    "HookEvaluation",
    "evaluate_hook_candidates",
    "generate_hook_candidates",
]
