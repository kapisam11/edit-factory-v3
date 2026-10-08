"""Human-quality editorial guardrails for autonomous publishing.

The guard is deliberately not an "AI detector".  It measures signals that tend to
make automated content feel generic: repeated templates, filler language,
synthetic-sounding openings, low specificity, and excessive similarity to recent
channel output.  A package must be materially different and useful before the
autonomous publisher will schedule it.

Public metadata is kept clean of product/vendor attribution.  Platform-required
disclosures are handled separately by the YouTube publisher and are never faked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from statistics import mean, pstdev
from typing import Any, Iterable, Mapping, Sequence


FILLER_PHRASES = (
    "in this video",
    "today we're going to",
    "today we are going to",
    "let's dive into",
    "let us dive into",
    "you won't believe",
    "you will not believe",
    "here's the thing",
    "here is the thing",
    "but here's what happened",
    "but here is what happened",
    "imagine this",
    "it all started when",
    "and then something amazing happened",
    "this changed everything",
    "shocking",
    "absolutely insane",
    "crazy story",
    "the real reason",
    "the hidden truth",
    "you need to see this",
    "wait until the end",
    "stay until the end",
    "trust me",
    "in today's video",
)

BRANDING_PATTERNS = (
    r"(?i)#?(?:edit\s*factory|editfactory|ai\s*video\s*factory|aivf)\b",
    r"(?i)\b(?:chatgpt|openai|claude|gemini|elevenlabs|midjourney)\b",
    r"(?i)\b(?:generated|created|written)\s+with\s+(?:ai|artificial intelligence)\b",
)

CLICKBAIT_PATTERNS = (
    r"you\s+won['’]?t\s+believe",
    r"\bshocking(?:!!!|!)?\b",
    r"\binsane(?:!!!|!)?\b",
    r"\bmust\s+watch(?:!!!|!)?\b",
    r"\bthis\s+changed\s+everything\b",
)

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’-]*")
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]?", re.UNICODE)


@dataclass(frozen=True)
class StyleAssessment:
    score: float
    publish_blocked: bool
    reasons: tuple[str, ...]
    metrics: Mapping[str, float]
    version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "publish_blocked": self.publish_blocked,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
            "version": self.version,
        }


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(str(text or ""))]


def _ngrams(tokens: Sequence[str], n: int = 3) -> set[tuple[str, ...]]:
    return {tuple(tokens[i : i + n]) for i in range(max(0, len(tokens) - n + 1))}


def ngram_overlap(left: str, right: str, *, n: int = 3) -> float:
    a = _ngrams(_tokens(left), n)
    b = _ngrams(_tokens(right), n)
    if not a or not b:
        return 0.0
    return round(len(a.intersection(b)) / max(1, min(len(a), len(b))), 4)


def _sentence_lengths(text: str) -> list[int]:
    return [len(_tokens(sentence)) for sentence in SENTENCE_RE.findall(str(text or "")) if _tokens(sentence)]


def _specificity_score(text: str) -> float:
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    numeric = sum(any(ch.isdigit() for ch in token) for token in TOKEN_RE.findall(text))
    long_tokens = sum(len(token) >= 8 for token in tokens)
    quoted = len(re.findall(r'["“”][^"“”]{8,}["“”]', text))
    proper_names = sum(1 for token in re.findall(r"\b[A-Z][a-z]{2,}\b", text))
    raw = (numeric * 1.5 + long_tokens * 0.25 + quoted * 1.5 + proper_names * 0.75) / max(1, len(tokens))
    return round(min(1.0, raw), 4)


def _cliche_hits(text: str) -> list[str]:
    lower = str(text or "").lower()
    return [phrase for phrase in FILLER_PHRASES if phrase in lower]


def sanitize_public_metadata(title: str, description: str, tags: Sequence[str]) -> dict[str, Any]:
    """Remove accidental tool/vendor attribution, never policy-required disclosure.

    This does not strip topic text such as "AI history". It only removes explicit
    generator/vendor boilerplate that the factory itself may have inserted.
    """
    def clean(value: str) -> str:
        result = str(value or "")
        for pattern in BRANDING_PATTERNS:
            result = re.sub(pattern, "", result)
        result = re.sub(r"\s{2,}", " ", result)
        result = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", result)
        return result.strip()

    clean_title = clean(title)
    clean_description = clean(description)
    clean_tags = [
        clean_tag = re.sub(r"\s{2,}", " ", str(tag).strip())
        for pattern in BRANDING_PATTERNS:
            clean_tag = re.sub(pattern, "", clean_tag)
        if clean_tag.strip()
    ]
    return {
        "title": clean_title,
        "description": clean_description,
        "tags": clean_tags,
        "branding_removed": (
            clean_title != str(title or "").strip()
            or clean_description != str(description or "").strip()
            or list(tags) != clean_tags
        ),
    }


def assess_text(
    text: str,
    *,
    recent_texts: Iterable[str] = (),
    min_words: int = 35,
    recent_overlap_block: float = 0.52,
) -> StyleAssessment:
    raw = str(text or "").strip()
    tokens = _tokens(raw)
    sentences = _sentence_lengths(raw)
    if not tokens:
        return StyleAssessment(0.0, True, ("script is empty",), {},)

    unique_ratio = len(set(tokens)) / max(1, len(tokens))
    specificity = _specificity_score(raw)
    if len(sentences) > 1 and pstdev(sentences) > 0:
        rhythm_variation = min(1.0, pstdev(sentences) / max(1.0, mean(sentences)))
    else:
        rhythm_variation = 0.0
    cliches = _cliche_hits(raw)
    repeated_ratio = 1.0 - (len(set(sentences)) / max(1, len(sentences))) if sentences else 1.0
    recent = [str(item) for item in recent_texts if str(item).strip()]
    overlaps = [ngram_overlap(raw, item) for item in recent]
    max_overlap = max(overlaps, default=0.0)
    clickbait_hits = sum(bool(re.search(pattern, raw, flags=re.I)) for pattern in CLICKBAIT_PATTERNS)

    score = (
        0.30 * min(1.0, unique_ratio / 0.55)
        + 0.22 * specificity
        + 0.18 * rhythm_variation
        + 0.20 * (1.0 - min(1.0, len(cliches) / 4.0))
        + 0.10 * (1.0 - min(1.0, repeated_ratio / 0.20))
    )
    reasons: list[str] = []
    if len(tokens) < min_words:
        reasons.append(f"script is too short ({len(tokens)} words; minimum {min_words})")
    if cliches:
        reasons.append("generic/filler phrasing: " + ", ".join(cliches[:4]))
    if repeated_ratio > 0.20:
        reasons.append("sentences repeat too heavily")
    if max_overlap >= recent_overlap_block:
        reasons.append(f"script is too similar to a recent upload ({max_overlap:.2f} n-gram overlap)")
    if clickbait_hits >= 2:
        reasons.append("excessive clickbait language")
    if specificity < 0.03 and len(tokens) >= min_words:
        reasons.append("script lacks concrete, topic-specific detail")

    blocked = bool(reasons)
    return StyleAssessment(
        score=round(max(0.0, min(1.0, score)), 4),
        publish_blocked=blocked,
        reasons=tuple(reasons),
        metrics={
            "word_count": float(len(tokens)),
            "lexical_diversity": round(unique_ratio, 4),
            "specificity": specificity,
            "rhythm_variation": round(rhythm_variation, 4),
            "cliche_count": float(len(cliches)),
            "repeated_sentence_ratio": round(repeated_ratio, 4),
            "max_recent_ngram_overlap": round(max_overlap, 4),
            "clickbait_hits": float(clickbait_hits),
        },
    )


def assess_package(
    *,
    script: str,
    title: str,
    description: str,
    recent_texts: Iterable[str] = (),
    min_score: float = 0.66,
) -> StyleAssessment:
    script_assessment = assess_text(script, recent_texts=recent_texts)
    title_assessment = assess_text(title, recent_texts=(), min_words=3)
    desc_assessment = assess_text(description, recent_texts=(), min_words=12)
    generic_title = any(re.search(pattern, title, flags=re.I) for pattern in CLICKBAIT_PATTERNS)
    reasons = list(script_assessment.reasons)
    if generic_title:
        reasons.append("title uses a generic/clickbait template")
    if desc_assessment.metrics.get("cliche_count", 0.0) >= 2:
        reasons.append("description contains too much template filler")
    combined = round(
        0.65 * script_assessment.score
        + 0.20 * title_assessment.score
        + 0.15 * desc_assessment.score,
        4,
    )
    if combined < min_score:
        reasons.append(f"combined human-quality score {combined:.2f} is below {min_score:.2f}")
    return StyleAssessment(
        score=combined,
        publish_blocked=bool(reasons),
        reasons=tuple(dict.fromkeys(reasons)),
        metrics={
            **{f"script_{key}": value for key, value in script_assessment.metrics.items()},
            "title_score": title_assessment.score,
            "description_score": desc_assessment.score,
            "combined_score": combined,
        },
    )


__all__ = [
    "StyleAssessment",
    "assess_text",
    "assess_package",
    "ngram_overlap",
    "sanitize_public_metadata",
]
