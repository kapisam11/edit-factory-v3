"""Deterministic editorial guardrails for human-feeling autonomous content.

This is not an AI detector and it does not claim that heuristics can prove who
created a script.  It blocks common failure modes that make automated videos
feel interchangeable: canned openings, filler, repeated CTAs, recycled hooks,
low specificity, weak sentence rhythm, clickbait templates, and high overlap
with recent channel output.

Public metadata sanitization removes accidental factory/vendor credits.  It
does not suppress source attribution or platform-required disclosure.
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
    "let's break it down",
    "let us break it down",
    "here's the thing",
    "here is the thing",
    "you won't believe",
    "you will not believe",
    "imagine this",
    "it all started when",
    "and then something amazing happened",
    "this changed everything",
    "the real reason",
    "the hidden truth",
    "you need to see this",
    "wait until the end",
    "stay until the end",
    "trust me",
    "in today's video",
    "in today's world",
    "in conclusion",
    "to sum it up",
    "so there you have it",
)

AUTOMATION_MARKERS = (
    "let's explore",
    "let us explore",
    "let's uncover",
    "let us uncover",
    "let's take a look",
    "let us take a look",
    "join me as we",
    "we're going to explore",
    "we are going to explore",
    "in this fascinating",
    "a fascinating journey",
    "without further ado",
)

CTA_MARKERS = (
    "like and subscribe",
    "subscribe for more",
    "follow for more",
    "smash that like button",
    "comment below",
    "let me know in the comments",
    "thanks for watching",
)

BRANDING_PATTERNS = (
    r"(?i)#?(?:edit\s*factory|editfactory|ai\s*video\s*factory|aivf)\b",
    r"(?i)\b(?:created|generated|written|edited|made|powered|produced)\s+"
    r"(?:by|with|using)\s+(?:ai|artificial intelligence)\b",
    r"(?i)\b(?:created|generated|written|edited|made|powered|produced)\s+"
    r"(?:by|with|using)\s+(?:chatgpt|openai|claude|gemini|elevenlabs|midjourney)\b",
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
TITLE_STRUCTURE_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "why", "how", "what", "when",
    "where", "who", "which", "still", "really", "actually", "matters",
    "happened", "next", "part", "detail", "moment", "behind", "from",
}


@dataclass(frozen=True)
class StyleAssessment:
    score: float
    publish_blocked: bool
    reasons: tuple[str, ...]
    metrics: Mapping[str, float]
    version: str = "2.0.0"

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


def token_jaccard(left: str, right: str) -> float:
    a = set(_tokens(left))
    b = set(_tokens(right))
    if not a or not b:
        return 0.0
    return round(len(a.intersection(b)) / max(1, len(a.union(b))), 4)


def topic_similarity(left: str, right: str) -> float:
    """Conservative lexical similarity for queue diversity decisions.

    Token containment/dice similarity catches paraphrases that reorder or add
    only a small number of words, while the n-gram score still catches near
    verbatim phrasing.  The guard intentionally prefers false positives over
    allowing a queue of near-duplicate topics.
    """
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    left_set = set(left_tokens)
    right_set = set(right_tokens)
    overlap = len(left_set.intersection(right_set))
    dice = (2.0 * overlap) / max(1, len(left_set) + len(right_set))
    containment = overlap / max(1, min(len(left_set), len(right_set)))
    return round(
        max(
            ngram_overlap(left, right, n=2),
            token_jaccard(left, right),
            dice,
            containment,
        ),
        4,
    )


def assess_topic_diversity(
    topic: str,
    recent_topics: Iterable[str] = (),
    *,
    min_similarity: float = 0.72,
) -> dict[str, Any]:
    clean = str(topic or "").strip()
    similarities = [
        topic_similarity(clean, item)
        for item in recent_topics
        if str(item).strip() and str(item).strip().casefold() != clean.casefold()
    ]
    maximum = max(similarities, default=0.0)
    blocked = bool(clean) and maximum >= float(min_similarity)
    return {
        "publish_blocked": blocked,
        "blocked": blocked,
        "max_recent_topic_similarity": maximum,
        "threshold": float(min_similarity),
    }


def _sentence_lengths(text: str) -> list[int]:
    return [
        len(_tokens(sentence))
        for sentence in SENTENCE_RE.findall(str(text or ""))
        if _tokens(sentence)
    ]


def _specificity_score(text: str) -> float:
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    numeric = sum(any(ch.isdigit() for ch in token) for token in TOKEN_RE.findall(text))
    long_tokens = sum(len(token) >= 8 for token in tokens)
    quoted = len(re.findall(r'["“”][^"“”]{8,}["“”]', text))
    proper_names = sum(1 for token in re.findall(r"\b[A-Z][a-z]{2,}\b", text))
    concrete_markers = sum(
        bool(re.search(pattern, text, flags=re.I))
        for pattern in (
            r"\b\d{4}\b",
            r"\b\d+(?:\.\d+)?\s*(?:km|miles|kg|%|million|billion)\b",
            r"\b(?:according to|reported by|documents show|records show)\b",
        )
    )
    raw = (
        numeric * 1.5
        + long_tokens * 0.20
        + quoted * 1.5
        + proper_names * 0.75
        + concrete_markers * 1.5
    ) / max(1, len(tokens))
    return round(min(1.0, raw), 4)


def _cliche_hits(text: str) -> list[str]:
    lower = str(text or "").lower()
    return [phrase for phrase in FILLER_PHRASES if phrase in lower]


def _automation_hits(text: str) -> list[str]:
    lower = str(text or "").lower()
    return [phrase for phrase in AUTOMATION_MARKERS if phrase in lower]


def _cta_hits(text: str) -> list[str]:
    lower = str(text or "").lower()
    return [phrase for phrase in CTA_MARKERS if phrase in lower]


def _opening(text: str) -> str:
    sentences = [sentence.strip() for sentence in SENTENCE_RE.findall(str(text or "")) if sentence.strip()]
    return " ".join(_tokens(" ".join(sentences[:2])))


def _title_structure(text: str) -> tuple[str, ...]:
    """Map topic-specific words to a single placeholder per phrase.

    Collapsing adjacent topic tokens prevents titles such as
    "Why Apollo 11 still matters" and "Why Challenger still matters" from
    escaping the repeated-template check only because one topic has two tokens.
    """
    structure: list[str] = []
    for token in _tokens(text):
        normalized = token if token in TITLE_STRUCTURE_STOPWORDS else "<topic>"
        if normalized == "<topic>" and structure and structure[-1] == "<topic>":
            continue
        structure.append(normalized)
    return tuple(structure)


def _title_structure_similarity(left: str, right: str) -> float:
    a = _title_structure(left)
    b = _title_structure(right)
    if not a or not b:
        return 0.0
    return round(SequenceMatcher(None, a, b).ratio(), 4)


def sanitize_public_metadata(title: str, description: str, tags: Sequence[str]) -> dict[str, Any]:
    """Remove explicit factory/vendor credits without rewriting topic meaning."""
    def clean(value: str) -> str:
        result = str(value or "")
        for pattern in BRANDING_PATTERNS:
            result = re.sub(pattern, "", result)
        result = re.sub(r"\s{2,}", " ", result)
        result = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", result)
        return result.strip()

    clean_title = clean(title)
    clean_description = clean(description)
    clean_tags: list[str] = []
    for tag in tags:
        clean_tag = clean(str(tag).strip())
        clean_tag = clean_tag.strip("#-: ")
        if clean_tag:
            clean_tags.append(clean_tag)

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
    min_words: int = 55,
    recent_overlap_block: float = 0.52,
    opening_reuse_block: float = 0.88,
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
    automation = _automation_hits(raw)
    ctas = _cta_hits(raw)
    repeated_ratio = 1.0 - (len(set(sentences)) / max(1, len(sentences))) if sentences else 1.0

    recent = [str(item) for item in recent_texts if str(item).strip()]
    overlaps = [ngram_overlap(raw, item) for item in recent]
    max_overlap = max(overlaps, default=0.0)

    opening = _opening(raw)
    opening_scores = [
        SequenceMatcher(None, opening, _opening(item)).ratio()
        for item in recent
        if _opening(item)
    ]
    max_opening_similarity = max(opening_scores, default=0.0)

    clickbait_hits = sum(bool(re.search(pattern, raw, flags=re.I)) for pattern in CLICKBAIT_PATTERNS)

    score = (
        0.25 * min(1.0, unique_ratio / 0.58)
        + 0.26 * min(1.0, specificity / 0.08)
        + 0.18 * rhythm_variation
        + 0.16 * (1.0 - min(1.0, len(cliches) / 3.0))
        + 0.08 * (1.0 - min(1.0, repeated_ratio / 0.20))
        + 0.07 * (1.0 - min(1.0, len(ctas) / 2.0))
    )

    reasons: list[str] = []
    if len(tokens) < min_words:
        reasons.append(f"script is too short ({len(tokens)} words; minimum {min_words})")
    if len(cliches) >= 2:
        reasons.append("generic/filler phrasing: " + ", ".join(cliches[:5]))
    if automation:
        reasons.append("templated narration markers: " + ", ".join(automation[:3]))
    if len(ctas) >= 2:
        reasons.append("repeated social-media CTA language")
    if repeated_ratio > 0.20:
        reasons.append("sentences repeat too heavily")
    if max_overlap >= recent_overlap_block:
        reasons.append(f"script is too similar to a recent upload ({max_overlap:.2f} n-gram overlap)")
    if max_opening_similarity >= opening_reuse_block:
        reasons.append(
            f"opening is too similar to a recent upload ({max_opening_similarity:.2f} similarity)"
        )
    if clickbait_hits >= 2:
        reasons.append("excessive clickbait language")
    if specificity < 0.045 and len(tokens) >= min_words:
        reasons.append("script lacks enough concrete, topic-specific detail")
    if len(sentences) >= 5 and rhythm_variation < 0.05:
        reasons.append("sentence rhythm is too uniform")
    if unique_ratio < 0.50 and len(tokens) >= min_words:
        reasons.append("vocabulary is too repetitive")

    return StyleAssessment(
        score=round(max(0.0, min(1.0, score)), 4),
        publish_blocked=bool(reasons),
        reasons=tuple(dict.fromkeys(reasons)),
        metrics={
            "word_count": float(len(tokens)),
            "lexical_diversity": round(unique_ratio, 4),
            "specificity": specificity,
            "rhythm_variation": round(rhythm_variation, 4),
            "cliche_count": float(len(cliches)),
            "automation_marker_count": float(len(automation)),
            "cta_count": float(len(ctas)),
            "repeated_sentence_ratio": round(repeated_ratio, 4),
            "max_recent_ngram_overlap": round(max_overlap, 4),
            "max_recent_opening_similarity": round(max_opening_similarity, 4),
            "clickbait_hits": float(clickbait_hits),
        },
    )


def assess_package(
    *,
    script: str,
    title: str,
    description: str,
    recent_texts: Iterable[str] = (),
    recent_titles: Iterable[str] = (),
    min_score: float = 0.72,
) -> StyleAssessment:
    script_assessment = assess_text(script, recent_texts=recent_texts)
    title_assessment = assess_text(title, recent_texts=(), min_words=3)
    desc_assessment = assess_text(description, recent_texts=(), min_words=12)

    generic_title = any(re.search(pattern, title, flags=re.I) for pattern in CLICKBAIT_PATTERNS)
    recent_title_values = [str(item).strip() for item in recent_titles if str(item).strip()]
    title_overlaps = [token_jaccard(title, item) for item in recent_title_values]
    title_structures = [_title_structure_similarity(title, item) for item in recent_title_values]
    max_title_overlap = max(title_overlaps, default=0.0)
    max_title_structure_similarity = max(title_structures, default=0.0)
    reasons = list(script_assessment.reasons)
    if generic_title:
        reasons.append("title uses a generic/clickbait template")
    if desc_assessment.metrics.get("cliche_count", 0.0) >= 2:
        reasons.append("description contains too much template filler")
    if max_title_overlap >= 0.82:
        reasons.append(f"title is too similar to a recent upload ({max_title_overlap:.2f})")
    if max_title_structure_similarity >= 0.92 and recent_title_values:
        reasons.append(
            "title repeats a recent template structure "
            f"({max_title_structure_similarity:.2f})"
        )

    combined = round(
        0.68 * script_assessment.score
        + 0.18 * title_assessment.score
        + 0.14 * desc_assessment.score,
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
            "max_recent_title_overlap": round(max_title_overlap, 4),
            "max_recent_title_structure_similarity": round(max_title_structure_similarity, 4),
            "combined_score": combined,
        },
    )


__all__ = [
    "StyleAssessment",
    "assess_text",
    "assess_package",
    "assess_topic_diversity",
    "ngram_overlap",
    "token_jaccard",
    "topic_similarity",
    "sanitize_public_metadata",
]
