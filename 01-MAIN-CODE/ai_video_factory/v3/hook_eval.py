"""Candidate generation and deterministic evidence-aware hook evaluation."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence


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
    score: float
    reasons: tuple[str, ...]
    evaluator: str = "deterministic_editorial_heuristic"
    version: str = "1.0.0"

    def to_dict(self) -> dict[str, object]:
        return {
            "clarity": self.clarity,
            "curiosity": self.curiosity,
            "specificity": self.specificity,
            "emotional_relevance": self.emotional_relevance,
            "novelty": self.novelty,
            "score": self.score,
            "reasons": list(self.reasons),
            "evaluator": self.evaluator,
            "version": self.version,
        }


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", str(text).lower()))


def generate_hook_candidates(
    topic: str,
    stakes: str,
    angle: str,
    watch_to_end_reason: str,
    edit_type: str,
) -> tuple[HookCandidate, ...]:
    topic_text = " ".join(str(topic).split()[:7])
    style = str(edit_type).lower()
    return (
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
) -> tuple[tuple[HookCandidate, HookEvaluation], ...]:
    topic_tokens = _tokens(topic)
    emotional_tokens = _tokens(f"{stakes} {angle} {watch_to_end_reason}")
    texts = [candidate.text for candidate in candidates]
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
        similarities = []
        for other in texts:
            if other == candidate.text:
                continue
            other_tokens = _tokens(other)
            similarities.append(len(words & other_tokens) / max(1, len(words | other_tokens)))
        novelty = max(0.0, 1.0 - max(similarities, default=0.0))
        score = (
            0.20 * clarity
            + 0.24 * curiosity
            + 0.22 * specificity
            + 0.22 * emotional_relevance
            + 0.12 * novelty
        )
        reasons = tuple(
            reason
            for reason, passed in (
                ("clear opening language", clarity >= 0.75),
                ("creates an information gap", curiosity >= 0.80),
                ("mentions the requested topic", bool(words & topic_tokens)),
                ("aligns with stated stakes", bool(words & emotional_tokens)),
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
                    round(score, 3),
                    reasons,
                ),
            )
        )
    return tuple(sorted(outputs, key=lambda pair: pair[1].score, reverse=True))


__all__ = [
    "HookCandidate",
    "HookEvaluation",
    "evaluate_hook_candidates",
    "generate_hook_candidates",
]
