"""Build polished, platform-aware upload packages from completed jobs."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .render_engine import run_ffprobe


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


def rank_title_candidates(topic: str, *, hook: str = "", strongest_angle: str = "", emotion: str = "", candidates: Optional[Sequence[str]] = None, max_candidates: int = 20, title_limit: int = 100) -> List[Dict[str, Any]]:
    """Generate and rank up to twenty deterministic title options."""
    base_topic = _normalize_phrase(topic)
    generated = list(candidates or [])
    angle = _normalize_phrase(strongest_angle)
    generated.extend([
        _normalize_phrase(hook),
        f"The real story behind {base_topic}",
        f"What really happened with {base_topic}",
        f"Nobody expected this: {base_topic}",
        f"The hidden truth about {base_topic}",
        f"Why {base_topic} changed everything",
        f"The moment {base_topic} went wrong",
        f"How {base_topic} became unforgettable",
        f"The biggest thing people miss about {base_topic}",
        f"What changed after {base_topic}",
        f"The one detail that changes {base_topic}",
        f"Before you watch {base_topic}, know this",
        f"The untold part of {base_topic}",
        f"The smartest way to understand {base_topic}",
        f"The mistake that changed {base_topic}",
        f"Why nobody saw {base_topic} coming",
        f"The hidden reason {base_topic} matters",
        f"The turning point in {base_topic}",
        f"What {base_topic} teaches us",
    ])
    if angle:
        generated.extend([f"{base_topic}: {angle}", f"The truth about {angle}"])
    if emotion:
        generated.append(f"The {emotion} story of {base_topic}")

    seen = set()
    ranked: List[Dict[str, Any]] = []
    topic_tokens = set(_topic_tokens(base_topic))
    for raw in generated:
        title = _normalize_phrase(raw).strip("-: ")
        if not title or len(title) > title_limit:
            continue
        key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        words = title.split()
        overlap = len(topic_tokens.intersection(_topic_tokens(title)))
        specificity = min(4.0, overlap * 1.5)
        readability = 2.0 if 4 <= len(words) <= 12 else (1.0 if len(words) <= 16 else 0.0)
        curiosity = _curiosity_score(title)
        information_gap = 1.5 if any(m in title.lower() for m in ("why", "how", "truth", "hidden", "real", "what really")) else 0.0
        clickbait_penalty = 1.25 if any(m in title.lower() for m in ("you won't believe", "shocking!!!")) else 0.0
        score = round(specificity + readability + curiosity + information_gap - clickbait_penalty, 3)
        ranked.append({"title": title, "score": score, "reasons": {"topic_overlap": overlap, "specificity": specificity, "readability": readability, "curiosity": curiosity, "information_gap": information_gap, "clickbait_penalty": clickbait_penalty}})
    ranked.sort(key=lambda item: (-item["score"], len(item["title"]), item["title"].lower()))
    return ranked[: max(1, int(max_candidates))]


def generate_platform_tags(title: str, description: str, topic: str, *, max_tags: int = 30) -> List[str]:
    text = " ".join((topic, title, description))
    tokens = _topic_tokens(text)
    topic_tokens = set(_topic_tokens(topic))
    scores: Dict[str, float] = {}
    for token in tokens:
        score = 1.0 + (2.0 if token in topic_tokens else 0.0) + (1.0 if token in title.lower().split() else 0.0)
        scores[token] = scores.get(token, 0.0) + score
    for phrase in re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]{2,}(?: [A-Za-z0-9][A-Za-z0-9'_-]{2,}){1,3}", text.lower()):
        phrase = _normalize_phrase(phrase)
        if phrase and phrase not in _GENERIC_TAGS:
            scores[phrase] = scores.get(phrase, 0.0) + 2.5
    ordered = sorted(scores, key=lambda item: (-scores[item], len(item), item))
    return ordered[: max(1, int(max_tags))]


def build_description(topic: str, summary: Mapping[str, Any], script: str, platform: str) -> str:
    angle = _normalize_phrase(str(summary.get("strongest_angle") or ""))
    why_care = _normalize_phrase(str(summary.get("why_care") or summary.get("why_viewers_care") or ""))
    emotion = _normalize_phrase(str(summary.get("emotion") or ""))
    pieces = [angle or _normalize_phrase(topic)]
    if why_care:
        pieces.append(why_care)
    if emotion:
        pieces.append(f"Tone: {emotion}.")
    if platform == "youtube_shorts":
        pieces.append("#shorts")
    description = "\n\n".join(piece for piece in pieces if piece)
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
    description = build_description(topic, summary, script, platform)
    tags = generate_platform_tags(chosen_title, description, topic, max_tags=profile.tag_limit)

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
        "artifacts": _collect_artifacts(root),
    }
    (upload_dir / "metadata.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (upload_dir / "README-UPLOAD.md").write_text(
        "# Upload package\n\n"
        f"Platform profile: `{profile.name}`\n\n"
        "1. Upload the listed video.\n2. Use `title.txt`, `description.txt`, and `tags.txt`.\n"
        "3. Use `captions.srt` when supplied.\n4. Review AI/altered-content disclosure before publishing.\n"
        "5. `metadata.json` contains all twenty ranked title candidates and artifact fingerprints.\n",
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


__all__ = ["OutputProfile", "OUTPUT_PROFILES", "get_output_profile", "rank_title_candidates", "generate_platform_tags", "build_description", "finalize_upload_package"]
