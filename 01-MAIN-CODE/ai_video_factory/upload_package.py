"""Build polished, platform-aware upload packages from completed jobs."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from fractions import Fraction
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .render_engine import run_ffprobe
from .rights_policy import rights_gate
from .factuality_guard import metadata_fact_gate
from .human_review import review_gate
from .human_style_guard import sanitize_public_metadata


@dataclass(frozen=True)
class OutputProfile:
    name: str
    width: int
    height: int
    aspect_ratio: str
    title_limit: int
    description_limit: int
    tag_limit: int


OUTPUT_PROFILES: Dict[str, OutputProfile] = {
    "youtube_shorts": OutputProfile("youtube_shorts", 1080, 1920, "9:16", 100, 5000, 30),
    "youtube": OutputProfile("youtube", 1920, 1080, "16:9", 100, 5000, 30),
    "tiktok": OutputProfile("tiktok", 1080, 1920, "9:16", 2200, 4000, 20),
    "instagram_reels": OutputProfile("instagram_reels", 1080, 1920, "9:16", 2200, 2200, 30),
    "square": OutputProfile("square", 1080, 1080, "1:1", 2200, 5000, 30),
}

_GENERIC_TAGS = {"youtube", "video", "trending", "viral"}
_STOPWORDS = {
    "about", "after", "again", "against", "being", "could", "from", "have", "into",
    "just", "more", "most", "that", "their", "there", "these", "they", "this", "what",
    "when", "where", "which", "while", "with", "would", "your", "will", "than", "then",
    "were", "been", "them", "only", "very", "here", "because", "story", "video",
}


def get_output_profile(name: str) -> OutputProfile:
    key = str(name or "youtube_shorts").strip().lower()
    if key not in OUTPUT_PROFILES:
        raise ValueError(f"Unknown output profile: {name!r}. Choose from {sorted(OUTPUT_PROFILES)}")
    return OUTPUT_PROFILES[key]


def _normalize_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _topic_tokens(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]{2,}", text.lower())
    return [token for token in tokens if token not in _STOPWORDS]


def _curiosity_score(text: str) -> float:
    lower = text.lower()
    score = sum(1.0 for marker in ("why", "how", "secret", "truth", "nobody", "hidden", "actually", "finally", "before", "after") if marker in lower)
    if "?" in text:
        score += 1.25
    if any(ch.isdigit() for ch in text):
        score += 0.35
    return min(5.0, score)


def _source_records(summary: Mapping[str, Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    seen = set()

    def add(raw: Mapping[str, Any], default_source: str = "") -> None:
        record = {
            "title": _normalize_phrase(str(raw.get("title") or raw.get("source_title") or "")),
            "creator": _normalize_phrase(str(raw.get("creator") or raw.get("uploader") or raw.get("source_creator") or raw.get("artist") or "")),
            "url": str(raw.get("url") or raw.get("source_url") or "").strip(),
            "license": _normalize_phrase(str(raw.get("license") or "")),
            "license_url": str(raw.get("license_url") or "").strip(),
            "rights_basis": _normalize_phrase(str(raw.get("rights_basis") or "")),
            "rights_status": _normalize_phrase(str(raw.get("rights_status") or "review_required")).lower(),
            "source": _normalize_phrase(str(raw.get("source") or default_source)).lower(),
            "evidence_url": str(raw.get("evidence_url") or raw.get("rights_evidence_url") or raw.get("permission_url") or "").strip(),
            "declared_by": _normalize_phrase(str(raw.get("declared_by") or "")),
            "declared_at": str(raw.get("declared_at") or "").strip(),
            "attribution": _normalize_phrase(str(raw.get("attribution") or "")),
            "asset_origin": _normalize_phrase(str(raw.get("asset_origin") or "unknown")).lower(),
            "commercial_use": _normalize_phrase(str(raw.get("commercial_use") or "review_required")).lower(),
            "permitted_uses": _normalize_phrase(str(raw.get("permitted_uses") or "")),
            "attribution_required": str(raw.get("attribution_required") or "").strip().lower() in {"1", "true", "yes", "on"},
        }
        key = record["url"] or (record["creator"], record["title"], record["source"])
        has_rights_declaration = any(
            str(record.get(field) or "").strip()
            for field in (
                "rights_basis", "rights_status", "evidence_url", "license_url",
                "declared_by", "declared_at", "commercial_use", "permitted_uses",
                "attribution",
            )
        ) or record.get("asset_origin") != "unknown" or record.get("attribution_required")
        required_user_declaration = bool(summary.get("requires_rights_declaration")) and record["source"] == "user_provided"
        if key and key not in seen and (
            record["title"] or record["creator"] or record["url"] or has_rights_declaration or required_user_declaration
        ):
            seen.add(key)
            records.append(record)

    supplied = summary.get("source_credits") or []
    if isinstance(supplied, Mapping):
        add(supplied)
    elif isinstance(supplied, Sequence) and not isinstance(supplied, (str, bytes)):
        for raw in supplied:
            if isinstance(raw, Mapping):
                add(raw)

    source_meta = summary.get("source_manifest") or summary.get("source_metadata")
    if isinstance(source_meta, Mapping):
        nested = source_meta.get("metadata")
        merged_source = dict(nested) if isinstance(nested, Mapping) else {}
        # Explicit typed manifest fields take precedence; nested metadata keeps
        # declaration details introduced after the original manifest schema.
        merged_source.update(dict(source_meta))
        add(merged_source, default_source="user_provided")

    visuals = summary.get("visuals") or []
    if isinstance(visuals, Sequence) and not isinstance(visuals, (str, bytes)):
        for raw in visuals:
            if isinstance(raw, Mapping) and str(raw.get("source") or "").lower() in {"youtube", "reddit", "wikimedia"}:
                add(raw)

    return records


def validate_metadata_quality(
    topic: str,
    selected_title: str,
    title_rankings: Sequence[Mapping[str, Any]],
    description: str,
    tags: Sequence[str],
    *,
    title_limit: int = 100,
) -> Dict[str, Any]:
    """Reject obviously weak upload metadata before it can be marked publish-ready."""
    topic_tokens = set(_topic_tokens(topic))
    title_tokens = set(_topic_tokens(selected_title))
    checks: Dict[str, bool] = {
        "title_present": bool(_normalize_phrase(selected_title)),
        "title_within_limit": len(selected_title) <= int(title_limit),
        "title_topic_specific": bool(topic_tokens.intersection(title_tokens)) if topic_tokens else True,
        "title_not_generic": selected_title.strip().lower() not in {
            "the real reason",
            "the hidden legend",
            "lost forever",
            "the real story",
        },
        "title_candidate_pool": len(title_rankings) >= 3,
        "description_present": len(_normalize_phrase(description)) >= 40,
        "tags_present": len(tags) >= 3,
        "tags_not_spammy": not any(str(tag).strip().lower() in _GENERIC_TAGS for tag in tags),
        "tags_topic_specific": any(
            set(_topic_tokens(str(tag))).intersection(topic_tokens)
            for tag in tags
        ) if topic_tokens else True,
    }
    issues = [
        name.replace("_", " ")
        for name, passed in checks.items()
        if not passed
    ]
    score = round(100.0 * sum(bool(value) for value in checks.values()) / max(1, len(checks)), 1)
    return {
        "passed": all(checks.values()),
        "score": score,
        "checks": checks,
        "issues": issues,
    }


def media_rights_report(summary: Mapping[str, Any]) -> Dict[str, Any]:
    records = _source_records(summary)
    # A required input-media declaration must produce a concrete record even
    # when it contains only rights fields; otherwise the gate could see no
    # external source at all and incorrectly allow publishing.
    if summary.get("requires_rights_declaration") and not any(
        str(item.get("source") or "").strip().lower() == "user_provided"
        for item in records
        if isinstance(item, Mapping)
    ):
        supplied = summary.get("source_metadata")
        raw = supplied if isinstance(supplied, Mapping) else {}
        records.append({
            "title": _normalize_phrase(str(raw.get("title") or "")),
            "creator": _normalize_phrase(str(raw.get("creator") or "")),
            "url": str(raw.get("url") or raw.get("source_url") or "").strip(),
            "license": _normalize_phrase(str(raw.get("license") or "")),
            "license_url": str(raw.get("license_url") or "").strip(),
            "rights_basis": _normalize_phrase(str(raw.get("rights_basis") or "")).lower(),
            "rights_status": _normalize_phrase(str(raw.get("rights_status") or "review_required")).lower(),
            "source": "user_provided",
            "evidence_url": str(raw.get("evidence_url") or "").strip(),
            "declared_by": _normalize_phrase(str(raw.get("declared_by") or "")),
            "declared_at": str(raw.get("declared_at") or "").strip(),
            "attribution": _normalize_phrase(str(raw.get("attribution") or "")),
            "asset_origin": _normalize_phrase(str(raw.get("asset_origin") or "unknown")).lower(),
            "commercial_use": _normalize_phrase(str(raw.get("commercial_use") or "review_required")).lower(),
            "permitted_uses": _normalize_phrase(str(raw.get("permitted_uses") or "")),
            "attribution_required": str(raw.get("attribution_required") or "").strip().lower() in {"1", "true", "yes", "on"},
        })

    external = [
        item for item in records
        if item.get("source") in {"youtube", "reddit", "wikimedia", "user_provided"}
        or item.get("creator")
        or item.get("url")
    ]

    if summary.get("requires_rights_declaration"):
        supplied = summary.get("source_metadata") or summary.get("source_manifest")
        if isinstance(supplied, Mapping) and isinstance(supplied.get("metadata"), Mapping):
            merged_declaration = dict(supplied.get("metadata") or {})
            merged_declaration.update(dict(supplied))
            supplied = merged_declaration
        declaration_present = isinstance(supplied, Mapping) and any(
            str(supplied.get(key) or "").strip()
            for key in ("rights_status", "rights_basis", "source_url", "url", "evidence_url", "declared_by", "declared_at")
        )
        if not declaration_present:
            external.append({
                "title": "Input media",
                "creator": "",
                "url": "",
                "license": "",
                "license_url": "",
                "rights_basis": "",
                "rights_status": "review_required",
                "source": "user_provided",
                "evidence_url": "",
                "declared_by": "",
                "declared_at": "",
                "attribution": "",
            })

    evidence_records = []
    for item in external:
        # rights_basis is the caller's explicit declaration. A legacy
        # rights_status is accepted only when no explicit basis was supplied.
        declared_basis = str(item.get("rights_basis") or "").strip().lower()
        legacy_status = str(item.get("rights_status") or "").strip().lower()
        if declared_basis:
            basis = declared_basis
        elif legacy_status == "cleared":
            basis = "review_required"
        else:
            basis = legacy_status or "review_required"
        evidence_records.append({
            **item,
            "asset_id": item.get("asset_id") or item.get("title") or item.get("url") or "source",
            "source": item.get("source") or "external",
            "rights_basis": basis,
        })

    evidence_gate = rights_gate(
        evidence_records,
        require_commercial_use=bool(summary.get("requires_commercial_rights_review")),
    ) if evidence_records else {
        "status": "not_declared",
        "publish_blocked": False,
        "checked": [],
        "evidence_contract": "not applicable",
    }
    unresolved = [
        item for item in evidence_gate.get("checked") or []
        if isinstance(item, Mapping) and item.get("errors")
    ]

    return {
        "status": "cleared" if external and not unresolved else ("review_required" if unresolved else "not_declared"),
        "publish_blocked": bool(unresolved),
        "requires_explicit_declaration": bool(external),
        "sources": records,
        "unverified_sources": unresolved,
        "rights_evidence_gate": evidence_gate,
        "message": (
            "Publishing is blocked until every third-party or user-provided source has a declared, valid rights basis."
            if unresolved
            else "No unresolved media rights were declared."
        ),
    }


def _source_credit_block(records: Sequence[Mapping[str, Any]]) -> str:
    lines: List[str] = []
    for item in records:
        title = str(item.get("title") or "").strip()
        creator = str(item.get("creator") or "").strip()
        url = str(item.get("url") or "").strip()
        license_name = str(item.get("license") or "").strip()
        label = title or "Source footage"
        if creator:
            label += f" — {creator}"
        lines.append(f"Source: {label}")
        if url:
            lines.append(f"Source link: {url}")
        if license_name:
            lines.append(f"License: {license_name}")
    return "\n".join(lines)


def rank_title_candidates(
    topic: str,
    *,
    hook: str = "",
    strongest_angle: str = "",
    emotion: str = "",
    candidates: Optional[Sequence[str]] = None,
    max_candidates: int = 20,
    title_limit: int = 100,
) -> List[Dict[str, Any]]:
    """Generate natural, topic-specific title options and rank them."""
    base_topic = _normalize_phrase(topic)
    generated = list(candidates or [])
    angle = _normalize_phrase(strongest_angle)
    hook_text = _normalize_phrase(hook)
    angle_short = " ".join(angle.split()[:10])
    generated.extend([
        hook_text,
        f"{base_topic}: {angle_short}" if angle_short else "",
        f"What actually happened with {base_topic}",
        f"The turning point in {base_topic}",
        f"The part of {base_topic} most people miss",
        f"Why {base_topic} still matters",
        f"{base_topic}: the detail that changes the story",
        f"{base_topic} — what happened next",
    ])
    emotion_templates = {
        "funny": [
            f"The funniest moment in {base_topic}",
            f"How {base_topic} went completely wrong",
        ],
        "dramatic": [
            f"The moment {base_topic} nearly fell apart",
            f"The decision that changed {base_topic}",
        ],
        "nostalgic": [
            f"Why {base_topic} is still remembered",
            f"The moment everyone remembers from {base_topic}",
        ],
        "inspiring": [
            f"How {base_topic} kept going",
            f"The comeback behind {base_topic}",
        ],
        "trust": [
            f"Who {base_topic} could really trust",
            f"The choice that tested {base_topic}",
        ],
        "curious": [
            f"The detail everyone missed in {base_topic}",
            f"What people get wrong about {base_topic}",
        ],
    }
    generated.extend(emotion_templates.get(emotion.lower(), []))

    seen = set()
    ranked: List[Dict[str, Any]] = []
    topic_tokens = set(_topic_tokens(base_topic))
    hook_tokens = set(_topic_tokens(hook_text))
    angle_tokens = set(_topic_tokens(angle))

    for raw in generated:
        title = _normalize_phrase(raw).strip("-: ")
        if not title or len(title) > title_limit:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        if key in seen:
            continue
        seen.add(key)

        words = title.split()
        tokens = set(_topic_tokens(title))
        overlap = len(topic_tokens.intersection(tokens))
        hook_overlap = len(hook_tokens.intersection(tokens))
        angle_overlap = len(angle_tokens.intersection(tokens))
        curiosity = _curiosity_score(title)
        readability = 2.0 if 5 <= len(words) <= 12 else (1.0 if 3 <= len(words) <= 15 else 0.0)
        specificity = min(4.5, overlap * 1.4 + angle_overlap * 0.8)
        continuity = min(2.0, hook_overlap * 0.75)

        generic_penalty = 0.0
        lower = title.lower()
        if lower in {"the real reason", "the hidden legend", "lost forever", "the real story"}:
            generic_penalty += 3.0
        if any(marker in lower for marker in ("you won't believe", "shocking!!!", "insane!!!", "must watch!!!")):
            generic_penalty += 2.0
        if title.count("!") > 1:
            generic_penalty += 1.0

        score = round(
            specificity + continuity + readability + curiosity - generic_penalty,
            3,
        )
        ranked.append({
            "title": title,
            "score": score,
            "reasons": {
                "topic_overlap": overlap,
                "hook_overlap": hook_overlap,
                "angle_overlap": angle_overlap,
                "specificity": specificity,
                "readability": readability,
                "curiosity": curiosity,
                "generic_penalty": generic_penalty,
            },
        })

    ranked.sort(key=lambda item: (-item["score"], len(item["title"]), item["title"].lower()))
    return ranked[: max(1, int(max_candidates))]


def generate_platform_tags(
    title: str,
    description: str,
    topic: str,
    *,
    max_tags: int = 30,
    source_records: Optional[Sequence[Mapping[str, Any]]] = None,
) -> List[str]:
    """Generate focused tags from the topic, title, and relevant source metadata."""
    chunks = [topic, title, description]
    for item in list(source_records or []):
        chunks.extend([str(item.get("creator") or ""), str(item.get("title") or "")])

    text = " ".join(chunks)
    topic_tokens = set(_topic_tokens(topic))
    title_tokens = set(_topic_tokens(title))
    scores: Dict[str, float] = {}

    for token in _topic_tokens(text):
        score = 1.0
        if token in topic_tokens:
            score += 3.0
        if token in title_tokens:
            score += 1.5
        scores[token] = scores.get(token, 0.0) + score

    for phrase in re.findall(
        r"[A-Za-z0-9][A-Za-z0-9'_-]{2,}(?: [A-Za-z0-9][A-Za-z0-9'_-]{2,}){1,3}",
        text.lower(),
    ):
        phrase = _normalize_phrase(phrase)
        if phrase and all(word not in _GENERIC_TAGS for word in phrase.split()):
            scores[phrase] = scores.get(phrase, 0.0) + (
                3.5 if any(word in topic_tokens for word in phrase.split()) else 1.5
            )

    ordered = sorted(scores, key=lambda item: (-scores[item], len(item), item))
    result: List[str] = []
    seen = set()
    for tag in ordered:
        tag = tag.strip("#")
        if len(tag) < 3 or tag in seen or len(tag) > 60:
            continue
        seen.add(tag)
        result.append(tag)
        if len(result) >= max(1, int(max_tags)):
            break
    return result


def build_description(topic: str, summary: Mapping[str, Any], script: str, platform: str) -> str:
    """Build a natural description and include source credits when third-party media is used."""
    angle = _normalize_phrase(str(summary.get("strongest_angle") or ""))
    why_care = _normalize_phrase(str(summary.get("why_care") or summary.get("why_viewers_care") or ""))
    hook = _normalize_phrase(str(summary.get("hook") or ""))
    payoff = _normalize_phrase(str(summary.get("payoff") or ""))
    records = _source_records(summary)

    opening = hook or angle or _normalize_phrase(topic)
    paragraphs = [opening]

    body = [
        part
        for part in (
            angle if angle and angle.lower() != opening.lower() else "",
            why_care,
            payoff,
        )
        if part
    ]
    if body:
        paragraphs.append(" ".join(body))
    elif script:
        script_lines = [line.strip() for line in str(script).splitlines() if line.strip()]
        if script_lines:
            paragraphs.append(" ".join(script_lines[:2]))

    credit = _source_credit_block(records)
    if credit:
        paragraphs.append("Credits / source information:\n" + credit)

    if platform == "youtube_shorts":
        paragraphs.append("#shorts")

    description = "\n\n".join(paragraphs)
    return description[: get_output_profile(platform).description_limit].rstrip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_media(video_path: Path) -> Dict[str, Any]:
    """Probe enough stream/container detail to support a publish-readiness gate."""
    try:
        result = run_ffprobe([
            "ffprobe", "-v", "error",
            "-show_entries",
            "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height,duration,avg_frame_rate,r_frame_rate",
            "-of", "json", str(video_path),
        ], timeout=30)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "ffprobe failed").strip())
        payload = json.loads(result.stdout or "{}")
        if not isinstance(payload, dict):
            raise RuntimeError("ffprobe returned a non-object JSON payload")
        return payload
    except Exception as exc:
        return {"available": False, "warning": f"ffprobe could not validate media: {type(exc).__name__}: {exc}"}


def _rate_fps(value: object) -> float | None:
    try:
        parsed = float(Fraction(str(value or "").strip()))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _media_contract_report(
    video_path: Path,
    probe: Mapping[str, Any],
    profile: OutputProfile,
    *,
    require_audio: bool | None = None,
) -> Dict[str, Any]:
    """Fail closed when the final media cannot meet the selected output profile."""
    checks: Dict[str, bool] = {}
    errors: list[str] = []
    warnings: list[str] = []
    if not video_path.is_file() or video_path.is_symlink():
        return {
            "passed": False,
            "checks": {"file_present": False},
            "errors": ["final video is missing or is not a regular non-symlink file"],
            "warnings": [],
            "expected": {"width": profile.width, "height": profile.height},
        }
    checks["file_present"] = video_path.stat().st_size > 0
    if not checks["file_present"]:
        errors.append("final video is empty")

    available = probe.get("available") is not False
    checks["ffprobe_available"] = available
    if not available:
        errors.append(str(probe.get("warning") or "ffprobe media inspection failed"))

    raw_streams = probe.get("streams") if available else []
    streams = [item for item in raw_streams if isinstance(item, Mapping)] if isinstance(raw_streams, list) else []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    checks["video_stream_present"] = video_stream is not None
    if video_stream is None:
        errors.append("final video has no video stream")

    fmt = probe.get("format") if isinstance(probe.get("format"), Mapping) else {}
    try:
        duration = float(fmt.get("duration") or (video_stream or {}).get("duration") or 0.0)
    except (TypeError, ValueError, OverflowError):
        duration = 0.0
    checks["duration_valid"] = math.isfinite(duration) and duration > 0
    if not checks["duration_valid"]:
        errors.append("final video duration is missing or invalid")

    try:
        width = int((video_stream or {}).get("width") or 0)
        height = int((video_stream or {}).get("height") or 0)
    except (TypeError, ValueError, OverflowError):
        width = height = 0
    checks["dimensions_match_profile"] = width == profile.width and height == profile.height
    if not checks["dimensions_match_profile"]:
        errors.append(
            f"final video dimensions {width}x{height} do not match "
            f"{profile.name} profile {profile.width}x{profile.height}"
        )

    actual_fps = None
    for candidate in ((video_stream or {}).get("avg_frame_rate"), (video_stream or {}).get("r_frame_rate")):
        actual_fps = _rate_fps(candidate)
        if actual_fps is not None:
            break
    try:
        expected_fps = float(os.environ.get("AIVF_OUTPUT_FPS", "30"))
        fps_tolerance = float(os.environ.get("AIVF_OUTPUT_FPS_TOLERANCE", "1.0"))
    except (TypeError, ValueError, OverflowError):
        expected_fps = 0.0
        fps_tolerance = -1.0
    fps_config_valid = math.isfinite(expected_fps) and expected_fps > 0 and math.isfinite(fps_tolerance) and fps_tolerance >= 0
    checks["frame_rate_configuration_valid"] = fps_config_valid
    if not fps_config_valid:
        errors.append("AIVF_OUTPUT_FPS must be positive and AIVF_OUTPUT_FPS_TOLERANCE must be non-negative")
    checks["frame_rate_matches_policy"] = bool(actual_fps is not None and fps_config_valid and abs(actual_fps - expected_fps) <= fps_tolerance)
    if actual_fps is None:
        errors.append("final video frame rate is missing or invalid")
    elif fps_config_valid and not checks["frame_rate_matches_policy"]:
        errors.append(
            f"final video frame rate {actual_fps:.3f} fps is outside the configured "
            f"{expected_fps:.3f} fps ± {fps_tolerance:.3f} fps"
        )

    try:
        max_output_bytes = int(os.environ.get("AIVF_MAX_OUTPUT_BYTES", str(5 * 1024**3)))
    except (TypeError, ValueError, OverflowError):
        max_output_bytes = 0
    try:
        size_bytes = int(fmt.get("size") or video_path.stat().st_size)
    except (TypeError, ValueError, OverflowError, OSError):
        size_bytes = 0
    checks["output_size_known"] = size_bytes > 0
    if size_bytes <= 0:
        errors.append("final video size is missing or invalid")
    checks["output_size_within_limit"] = size_bytes > 0 and max_output_bytes > 0 and size_bytes <= max_output_bytes
    if max_output_bytes <= 0:
        errors.append("AIVF_MAX_OUTPUT_BYTES must be a positive integer")
    elif size_bytes > max_output_bytes:
        errors.append(f"final video size {size_bytes} exceeds configured {max_output_bytes}-byte limit")

    audio_required = (
        bool(require_audio)
        if require_audio is not None
        else str(os.environ.get("AIVF_REQUIRE_OUTPUT_AUDIO", "0")).strip().lower() not in {"0", "false", "no"}
    )
    checks["audio_present_when_required"] = not audio_required or bool(audio_streams)
    if audio_required and not audio_streams:
        errors.append("output audio is required but the final video has no audio stream")
    elif not audio_streams:
        warnings.append("final video has no audio stream; audio is optional under current configuration")

    format_names = str(fmt.get("format_name") or "")
    checks["container_identified"] = bool(format_names)
    if not format_names:
        errors.append("final video container format is missing")

    return {
        "passed": not errors,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "probe": {
            "duration_seconds": duration,
            "size_bytes": size_bytes,
            "width": width,
            "height": height,
            "frame_rate_fps": actual_fps,
            "has_audio": bool(audio_streams),
            "video_codec": (video_stream or {}).get("codec_name"),
            "audio_codecs": [item.get("codec_name") for item in audio_streams],
            "format_name": format_names,
        },
        "expected": {
            "profile": profile.name,
            "width": profile.width,
            "height": profile.height,
            "frame_rate_fps": expected_fps,
            "frame_rate_tolerance_fps": fps_tolerance,
            "max_output_bytes": max_output_bytes,
            "audio_required": audio_required,
        },
        "human_caption_visibility_review_required": True,
    }


def _ass_to_srt(ass_text: str) -> str:
    rows = []

    def clean_time(value: str) -> str:
        match = re.fullmatch(r"\s*(\d+):(\d{2}):(\d{2})[.](\d{1,3})\s*", value)
        if not match:
            raise ValueError(f"invalid ASS timestamp: {value!r}")
        hours, minutes, seconds, fraction = match.groups()
        # ASS normally stores centiseconds; pad rather than emitting invalid
        # two-digit millisecond values in an SRT timestamp.
        milliseconds = int(fraction.ljust(3, "0")[:3])
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d},{milliseconds:03d}"

    for line in ass_text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        try:
            start = clean_time(parts[1])
            end = clean_time(parts[2])
        except ValueError as exc:
            # Never silently drop a dialogue cue and then package the remainder
            # as if the captions were complete and valid.
            raise ValueError(f"invalid ASS dialogue timing on line: {line[:160]}") from exc
        text = re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", " ").strip()
        if not text:
            raise ValueError(f"empty ASS dialogue text on line: {line[:160]}")
        rows.append((start, end, text))
    return "\n\n".join(f"{i}\n{start} --> {end}\n{text}" for i, (start, end, text) in enumerate(rows, start=1)) + ("\n" if rows else "")


def _collect_artifacts(package_dir: Path) -> Dict[str, Any]:
    artifacts: Dict[str, Any] = {}
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file() or "upload" in path.parts:
            continue
        rel = path.relative_to(package_dir).as_posix()
        try:
            artifacts[rel] = {"sha256": _sha256(path), "size": path.stat().st_size}
        except OSError:
            continue
    return artifacts


def _human_review_required() -> bool:
    """Keep publish approval fail-closed outside explicit test mode."""
    env = os.environ.get("AIVF_ENV", "").strip().lower()
    override = os.environ.get("AIVF_REQUIRE_HUMAN_REVIEW")
    if env != "test":
        return True
    if override is None:
        return True
    return override.strip().lower() not in {"0", "false", "no", "off"}


def finalize_upload_package(package_dir: str, *, topic: str, summary: Optional[Mapping[str, Any]] = None, script: str = "", platform: str = "youtube_shorts", final_video: Optional[str] = None, thumbnail: Optional[str] = None, caption_path: Optional[str] = None) -> Dict[str, Any]:
    """Create the full copy/paste upload bundle for one platform."""
    profile = get_output_profile(platform)
    root = Path(package_dir)
    root.mkdir(parents=True, exist_ok=True)
    summary = dict(summary or {})
    gaming_profile = summary.get("gaming_profile")
    is_gaming = isinstance(gaming_profile, Mapping) and bool(gaming_profile.get("enabled", True))
    if is_gaming:
        # Own capture does not by itself establish commercial-use permission for game IP.
        summary["requires_rights_declaration"] = True
        summary["requires_commercial_rights_review"] = True
    hooks = summary.get("hooks") or []
    hook = str(summary.get("hook") or (hooks[0].get("hook") if hooks and isinstance(hooks[0], dict) else ""))
    final_metadata = summary.get("final_metadata") or {}
    title_rankings = rank_title_candidates(
        topic,
        hook=hook,
        strongest_angle=str(summary.get("strongest_angle") or ""),
        emotion=str(summary.get("emotion") or ""),
        candidates=final_metadata.get("title_candidates") if isinstance(final_metadata, Mapping) else None,
        title_limit=profile.title_limit,
        max_candidates=20,
    )
    chosen_title = str(final_metadata.get("selected_title") or (title_rankings[0]["title"] if title_rankings else _normalize_phrase(topic)[: profile.title_limit]))
    rights = media_rights_report(summary)
    source_records = rights["sources"]
    description = str(final_metadata.get("description") or build_description(topic, summary, script, platform))
    final_tags = final_metadata.get("hashtags") if isinstance(final_metadata, Mapping) else None
    tags = list(final_tags) if isinstance(final_tags, Sequence) and not isinstance(final_tags, (str, bytes)) else generate_platform_tags(
        chosen_title,
        description,
        topic,
        max_tags=profile.tag_limit,
        source_records=source_records,
    )
    public_metadata = sanitize_public_metadata(chosen_title, description, tags)
    chosen_title = str(public_metadata["title"])
    description = str(public_metadata["description"])
    tags = [str(tag) for tag in public_metadata["tags"]]
    metadata_quality = validate_metadata_quality(
        topic,
        chosen_title,
        title_rankings,
        description,
        tags,
        title_limit=profile.title_limit,
    )
    evidence = summary.get("factual_evidence") or summary.get("research_evidence") or []
    factuality = metadata_fact_gate(
        chosen_title,
        description,
        evidence=evidence if isinstance(evidence, Sequence) and not isinstance(evidence, (str, bytes)) else [],
        fact_reviewed=bool(summary.get("facts_reviewed", False)),
    )
    human_review = review_gate(summary.get("human_review"))
    require_human_review = _human_review_required()
    human_review["required"] = require_human_review
    upload_dir = root / "upload" / profile.name
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "title.txt").write_text(chosen_title + "\n", encoding="utf-8")
    (upload_dir / "description.txt").write_text(description + "\n", encoding="utf-8")
    (upload_dir / "tags.txt").write_text(", ".join(tags) + "\n", encoding="utf-8")

    # Keep a stable copy/paste bundle at upload/ for legacy callers while the
    # platform-specific subdirectory remains the canonical package location.
    legacy_upload_dir = root / "upload"
    if legacy_upload_dir != upload_dir:
        (legacy_upload_dir / "title.txt").write_text(chosen_title + "\n", encoding="utf-8")
        (legacy_upload_dir / "description.txt").write_text(description + "\n", encoding="utf-8")
        (legacy_upload_dir / "tags.txt").write_text(", ".join(tags) + "\n", encoding="utf-8")

    video = Path(final_video) if final_video else root / "final.mp4"
    video = video if video.is_absolute() else root / video
    thumb = Path(thumbnail) if thumbnail else None
    if thumb and not thumb.is_absolute():
        thumb = root / thumb
    caption = Path(caption_path) if caption_path else None
    if caption and not caption.is_absolute():
        caption = root / caption

    srt_path: Optional[Path] = None
    if caption and caption.exists():
        if caption.suffix.lower() == ".srt":
            srt_path = caption
        elif caption.suffix.lower() == ".ass":
            srt_path = upload_dir / "captions.srt"
            srt_path.write_text(_ass_to_srt(caption.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")

    files = {
        "video": str(video.relative_to(root)) if video.exists() else None,
        "thumbnail": str(thumb.relative_to(root)) if thumb and thumb.exists() else None,
        "captions_srt": str(srt_path.relative_to(root)) if srt_path and srt_path.exists() else None,
    }
    media_probe = _probe_media(video) if video.exists() else {"available": False, "warning": "final video not found"}
    media_contract = _media_contract_report(
        video,
        media_probe,
        profile,
        require_audio=True if is_gaming else None,
    )
    if caption_path is not None and not (srt_path is not None and srt_path.is_file() and srt_path.stat().st_size > 0):
        media_contract["passed"] = False
        media_contract["checks"]["caption_file_valid"] = False
        media_contract["errors"].append("a caption file was supplied but no non-empty validated SRT was produced")
    else:
        media_contract["checks"]["caption_file_valid"] = True

    from .media_quality_gates import audio_signal_report, validate_srt_file

    duration_value = (media_probe.get("format") or {}).get("duration") if isinstance(media_probe.get("format"), Mapping) else None
    if not duration_value and isinstance(media_probe.get("streams"), list):
        first_video = next((item for item in media_probe["streams"] if isinstance(item, Mapping) and item.get("codec_type") == "video"), {})
        duration_value = first_video.get("duration")
    try:
        final_duration = float(duration_value or 0.0)
    except (TypeError, ValueError, OverflowError):
        final_duration = 0.0

    if srt_path is not None and srt_path.is_file() and srt_path.stat().st_size > 0 and media_contract.get("checks", {}).get("duration_valid"):
        caption_quality = validate_srt_file(
            srt_path,
            duration_seconds=final_duration,
            max_line_chars=42 if profile.height > profile.width else 56,
            max_lines=2,
        )
    else:
        caption_quality = {
            "passed": caption_path is None,
            "status": "not_supplied" if caption_path is None else "blocked",
            "cue_count": 0,
            "errors": [] if caption_path is None else [{"code": "caption_file_invalid", "message": "caption file was not available for validation"}],
            "warnings": [{"code": "manual_caption_review", "message": "SRT checks cannot certify burned-in caption visibility or platform safe margins"}],
            "human_visibility_review_required": True,
            "safe_area_check": "not_certified_from_SRT; review rendered frames at phone size",
        }
    media_contract["caption_quality"] = caption_quality
    media_contract["checks"]["caption_timing_and_text_fit"] = bool(caption_quality.get("passed"))
    for issue in caption_quality.get("errors") or []:
        when = issue.get("timestamp_seconds")
        timestamp_text = f" at {float(when):.2f}s" if isinstance(when, (int, float)) else ""
        media_contract["errors"].append(
            f"caption QC [{issue.get('code', 'invalid')}]{timestamp_text}: {issue.get('message', 'caption validation failed')}"
        )
    if not caption_quality.get("passed"):
        media_contract["passed"] = False

    audio_quality_enabled_raw = os.environ.get("AIVF_ENABLE_AUDIO_SIGNAL_QC")
    audio_quality_enabled = (
        str(audio_quality_enabled_raw).strip().lower() not in {"0", "false", "no", "off"}
        if audio_quality_enabled_raw is not None
        else os.environ.get("AIVF_ENV", "development").strip().lower() != "test"
    )
    audio_present = bool((media_contract.get("probe") or {}).get("has_audio"))
    if audio_quality_enabled and audio_present and media_contract.get("checks", {}).get("ffprobe_available"):
        audio_quality = audio_signal_report(video, is_gaming=is_gaming)
    else:
        reason = "test mode default" if not audio_quality_enabled else "no validated audio stream available"
        audio_quality = {
            "passed": not (is_gaming and audio_quality_enabled),
            "status": "not_run",
            "metrics": {},
            "errors": ([{"code": "audio_signal_qc_not_run", "message": reason}] if is_gaming and audio_quality_enabled else []),
            "warnings": [{"code": "manual_audio_review", "message": "voice/music balance and perceived clarity still require listening review"}],
            "human_listening_review_required": True,
        }
    media_contract["audio_quality"] = audio_quality
    media_contract["checks"]["audio_signal_qc"] = bool(audio_quality.get("passed"))
    for issue in audio_quality.get("errors") or []:
        when = issue.get("timestamp_seconds")
        timestamp_text = f" at {float(when):.2f}s" if isinstance(when, (int, float)) else ""
        media_contract["errors"].append(
            f"audio QC [{issue.get('code', 'invalid')}]{timestamp_text}: {issue.get('message', 'audio validation failed')}"
        )
    if not audio_quality.get("passed"):
        media_contract["passed"] = False
    media_contract["human_caption_visibility_review_required"] = True
    disclosure = {
        "status": "review_required",
        "reason": "Review AI/altered-content disclosure controls before publishing.",
    }
    manifest = {
        "schema_version": 2,
        "topic": topic,
        "platform": profile.name,
        "profile": asdict(profile),
        "selected_title": chosen_title,
        "title_candidates": title_rankings,
        "description": description,
        "tags": tags,
        "files": files,
        "media_probe": media_probe,
        "media_contract": media_contract,
        "ai_disclosure": disclosure,
        "media_rights": rights,
        "metadata_quality": metadata_quality,
        "factuality": factuality,
        "human_review": human_review,
        "publish_ready": (
            bool(media_contract["passed"])
            and bool(metadata_quality["passed"])
            and not rights["publish_blocked"]
            and not factuality["publish_blocked"]
            and not (is_gaming and bool(gaming_profile.get("publish_blocked")))
            and (not require_human_review or human_review["status"] == "approved")
        ),
        "artifacts": _collect_artifacts(root),
    }
    metadata_text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    (upload_dir / "metadata.json").write_text(metadata_text, encoding="utf-8")
    (upload_dir / "README-UPLOAD.md").write_text(
        "# Upload package\n\n"
        f"Platform profile: `{profile.name}`\n\n"
        "1. Upload the listed video.\n2. Use `title.txt`, `description.txt`, and `tags.txt`.\n"
        "3. Use `captions.srt` when supplied.\n4. Review AI/altered-content disclosure before publishing.\n"
        "5. Review `metadata.json` for ranked titles, source credits, rights status, and artifact fingerprints.\n"
        "6. Do not publish while `media_rights.publish_blocked` is true.\n",
        encoding="utf-8",
    )
    if legacy_upload_dir != upload_dir:
        (legacy_upload_dir / "metadata.json").write_text(metadata_text, encoding="utf-8")
        (legacy_upload_dir / "README-UPLOAD.md").write_text(
            "# Upload package\n\n"
            "Use title.txt, description.txt, tags.txt, and metadata.json from this directory.\n"
            "Review AI/altered-content disclosure and rights status before publishing.\n",
            encoding="utf-8",
        )
    root_manifest = root / "upload_package.json"
    existing = {}
    if root_manifest.exists():
        try:
            existing = json.loads(root_manifest.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.setdefault("platforms", {})[profile.name] = manifest
    root_manifest.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


__all__ = ["OutputProfile", "OUTPUT_PROFILES", "get_output_profile", "rank_title_candidates", "generate_platform_tags", "build_description", "media_rights_report", "finalize_upload_package"]
