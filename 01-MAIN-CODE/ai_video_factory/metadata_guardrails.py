"""Deterministic metadata quality guardrails for upload packaging."""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Mapping, Sequence

from .factuality_guard import metadata_fact_gate

def _clean(text: str, max_length: int) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", str(text or ""))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_length].strip()

def normalize_title(topic: str, hook: str = "", max_length: int = 100) -> str:
    value = _clean(hook or topic, max_length)
    value = re.sub(r"^[\W_]+|[\W_]+$", "", value)
    return (value or "Edit Factory Short")[:max_length]

def hashtags_from_text(text: str, limit: int = 8) -> list[str]:
    stop = {"this","that","with","from","your","have","what","when","they","into","then","just","edit"}
    words = re.findall(r"[A-Za-z0-9]{3,30}", str(text or "").lower())
    counts = Counter(word for word in words if word not in stop)
    return [f"#{word}" for word, _ in counts.most_common(max(1, int(limit)))]

def build_description(topic: str, summary: Mapping[str, Any] | None = None, attribution: str = "") -> str:
    data = summary or {}
    parts = [_clean(str(data.get("hook") or topic), 180)]
    for key, limit in (("strongest_angle", 240), ("payoff", 300)):
        value = _clean(str(data.get(key) or ""), limit)
        if value:
            parts.append(value)
    if attribution.strip():
        parts.append(attribution.strip())
    return _clean("\n\n".join(parts), 5000)

def duplicate_phrase_score(values: Sequence[str], phrase_words: int = 3) -> float:
    """Measure repetition inside individual fields, not intentional title/topic overlap."""
    n = max(2, int(phrase_words))
    ratios = []
    for value in values:
        words = re.findall(r"[a-z0-9]+", str(value).lower())
        grams = [tuple(words[i:i+n]) for i in range(max(0, len(words) - n + 1))]
        if grams:
            repeated = sum(count - 1 for count in Counter(grams).values() if count > 1)
            ratios.append(repeated / max(1, len(grams)))
    return round(min(1.0, sum(ratios) / max(1, len(ratios))), 4)

def metadata_quality_score(title: str, description: str, hashtags: Sequence[str]) -> float:
    score = 0.35 * (12 <= len(title.strip()) <= 100)
    score += 0.30 * (80 <= len(description.strip()) <= 5000)
    score += 0.20 * (2 <= len(hashtags) <= 8)
    valid = sum(bool(re.fullmatch(r"#[A-Za-z0-9_]{2,40}", str(tag))) for tag in hashtags)
    score += 0.15 * (valid / max(1, len(hashtags)))
    return round(min(1.0, score), 4)

def validate_metadata(title: str, description: str, hashtags: Sequence[str]) -> dict[str, Any]:
    errors: list[str] = []
    if not 1 <= len(title.strip()) <= 100:
        errors.append("title must be 1..100 characters")
    if len(description.strip()) > 5000:
        errors.append("description exceeds 5000 characters")
    for tag in hashtags:
        if not re.fullmatch(r"#[A-Za-z0-9_]{2,40}", str(tag)):
            errors.append(f"invalid hashtag: {tag}")
    repeated = duplicate_phrase_score([title, description, *hashtags])
    if repeated > 0.25:
        errors.append("metadata contains excessive repeated phrases")
    return {
        "ok": not errors,
        "errors": errors,
        "score": metadata_quality_score(title, description, hashtags),
        "duplicate_phrase_score": repeated,
        "title_length": len(title),
        "description_length": len(description),
        "hashtag_count": len(hashtags),
    }

def build_upload_metadata(
    topic: str,
    summary: Mapping[str, Any] | None = None,
    hook: str = "",
    attribution: str = "",
) -> dict[str, Any]:
    data = summary or {}
    title = normalize_title(topic, hook)
    description = build_description(topic, data, attribution)
    text = " ".join([topic, hook, str(data.get("strongest_angle", ""))])
    hashtags = hashtags_from_text(text)
    quality = validate_metadata(title, description, hashtags)
    evidence = data.get("factual_evidence") or data.get("research_evidence") or []
    factuality = metadata_fact_gate(
        title,
        description,
        evidence=evidence if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)) else [],
        fact_reviewed=bool(data.get("facts_reviewed", False)),
    )
    return {
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "factuality": factuality,
        "quality": quality,
        "publish_ready": bool(quality["ok"]) and not factuality["publish_blocked"],
    }

def source_attribution_note(source: Mapping[str, Any]) -> str:
    status = str(source.get("rights_status") or "review_required")
    url = str(source.get("source_url") or "")
    license_name = str(((source.get("license") or {}).get("name")) or "")
    if status in {"owned", "explicit_permission"}:
        return ""
    parts = [f"Source: {url}" if url else "Source: unlisted"]
    if license_name and license_name != "unknown":
        parts.append(f"License: {license_name}")
    parts.append("Rights status: review_required" if status in {"unverified", "review_required"} else f"Rights status: {status}")
    return " | ".join(parts)

__all__ = [
    "normalize_title", "hashtags_from_text", "build_description",
    "duplicate_phrase_score", "metadata_quality_score", "validate_metadata",
    "build_upload_metadata", "source_attribution_note",
]
