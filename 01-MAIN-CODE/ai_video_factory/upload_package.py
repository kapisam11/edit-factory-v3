"""Build polished, platform-aware upload packages from completed jobs."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .render_engine import run_ffprobe
from .rights_policy import rights_gate
from .factuality_guard import metadata_fact_gate
from .human_review import review_gate


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
            "evidence_url": str(raw.get("evidence_url") or raw.get("permission_url") or "").strip(),
            "declared_by": _normalize_phrase(str(raw.get("declared_by") or "")),
            "declared_at": str(raw.get("declared_at") or "").strip(),
            "attribution": _normalize_phrase(str(raw.get("attribution") or "")),
        }
        key = record["url"] or (record["creator"], record["title"], record["source"])
        if key and key not in seen and (record["title"] or record["creator"] or record["url"]):
            seen.add(key)
            records.append(record)

    supplied = summary.get("source_credits") or []
    if isinstance(supplied, Mapping):
        add(supplied)
    elif isinstance(supplied, Sequence) and not isinstance(supplied, (str, bytes)):
        for raw in supplied:
            if isinstance(raw, Mapping):
                add(raw)

    source_meta = summary.get("source_metadata")
    if isinstance(source_meta, Mapping):
        add(source_meta, default_source="user_provided")

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
    external = [
        item for item in records
        if item.get("source") in {"youtube", "reddit", "wikimedia"}
        or item.get("creator")
        or item.get("url")
    ]

    if summary.get("requires_rights_declaration"):
        supplied = summary.get("source_metadata")
        if not isinstance(supplied, Mapping):
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

    strict_rights = os.environ.get("AIVF_STRICT_RIGHTS", "1") == "1"
    evidence_records = []
    for item in external:
        # Rights status is the only field allowed to declare the intended basis
        # at this boundary. A free-form rights_basis must never grant approval.
        evidence_records.append({
            **item,
            "asset_id": item.get("asset_id") or item.get("title") or item.get("url") or "source",
            "source": item.get("source") or "external",
            "rights_basis": item.get("rights_status") or "review_required",
        })

    evidence_gate = rights_gate(evidence_records, strict=strict_rights) if evidence_records else {
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
        "publish_blocked": bool(unresolved) and strict_rights,
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
    try:
        result = run_ffprobe([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-show_entries", "stream=index,codec_type,codec_name,width,height,duration",
            "-of", "json", str(video_path),
        ], timeout=20)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "ffprobe failed").strip())
        return json.loads(result.stdout or "{}")
    except Exception as exc:
        return {"available": False, "warning": f"ffprobe unavailable: {exc}"}


def _ass_to_srt(ass_text: str) -> str:
    rows = []
    for line in ass_text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        if len(parts) < 10:
            continue
        def clean_time(value: str) -> str:
            value = value.strip().replace(".", ",")
            if value.count(":") == 1:
                value = "0:" + value
            return value
        rows.append((clean_time(parts[1]), clean_time(parts[2]), re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", " ").strip()))
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


def finalize_upload_package(package_dir: str, *, topic: str, summary: Optional[Mapping[str, Any]] = None, script: str = "", platform: str = "youtube_shorts", final_video: Optional[str] = None, thumbnail: Optional[str] = None, caption_path: Optional[str] = None) -> Dict[str, Any]:
    """Create the full copy/paste upload bundle for one platform."""
    profile = get_output_profile(platform)
    root = Path(package_dir)
    root.mkdir(parents=True, exist_ok=True)
    summary = summary or {}
    hooks = summary.get("hooks") or []
    hook = str(summary.get("hook") or (hooks[0].get("hook") if hooks and isinstance(hooks[0], dict) else ""))
    title_rankings = rank_title_candidates(topic, hook=hook, strongest_angle=str(summary.get("strongest_angle") or ""), emotion=str(summary.get("emotion") or ""), title_limit=profile.title_limit, max_candidates=20)
    chosen_title = title_rankings[0]["title"] if title_rankings else _normalize_phrase(topic)[: profile.title_limit]
    rights = media_rights_report(summary)
    source_records = rights["sources"]
    description = build_description(topic, summary, script, platform)
    tags = generate_platform_tags(chosen_title, description, topic, max_tags=profile.tag_limit, source_records=source_records)
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
    require_human_review = os.environ.get("AIVF_REQUIRE_HUMAN_REVIEW", "1") == "1"
    human_review["required"] = require_human_review
    upload_dir = root / "upload" / profile.name
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / "title.txt").write_text(chosen_title + "\n", encoding="utf-8")
    (upload_dir / "description.txt").write_text(description + "\n", encoding="utf-8")
    (upload_dir / "tags.txt").write_text(", ".join(tags) + "\n", encoding="utf-8")

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
        "ai_disclosure": disclosure,
        "media_rights": rights,
        "metadata_quality": metadata_quality,
        "factuality": factuality,
        "human_review": human_review,
        "publish_ready": (
            bool(metadata_quality["passed"])
            and not rights["publish_blocked"]
            and not factuality["publish_blocked"]
            and (not require_human_review or human_review["status"] == "approved")
        ),
        "artifacts": _collect_artifacts(root),
    }
    (upload_dir / "metadata.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (upload_dir / "README-UPLOAD.md").write_text(
        "# Upload package\n\n"
        f"Platform profile: `{profile.name}`\n\n"
        "1. Upload the listed video.\n2. Use `title.txt`, `description.txt`, and `tags.txt`.\n"
        "3. Use `captions.srt` when supplied.\n4. Review AI/altered-content disclosure before publishing.\n"
        "5. Review `metadata.json` for ranked titles, source credits, rights status, and artifact fingerprints.\n"
        "6. Do not publish while `media_rights.publish_blocked` is true.\n",
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
