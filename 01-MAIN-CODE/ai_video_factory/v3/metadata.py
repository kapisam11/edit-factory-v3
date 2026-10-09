"""Final-content-driven metadata generation for V3 packaging."""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Mapping


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]{2,}", str(text or "").lower())


def _clean(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    return value[:limit].strip()


def generate_final_metadata(
    manifest: Mapping[str, Any],
    *,
    source_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    topic = _clean(str(manifest.get("topic", "")), 100)
    hook = _clean(str(manifest.get("hook", "")), 100)
    overlays = [
        _clean(str(value), 80)
        for value in (manifest.get("overlays") or [])
        if _clean(str(value), 80)
    ]
    purposes = [
        _clean(str(value), 60)
        for value in (manifest.get("clip_purposes") or [])
        if _clean(str(value), 60)
    ]
    scenes = [item for item in (manifest.get("source_scenes") or []) if isinstance(item, Mapping)]
    search_terms = [
        _clean(str(value).lstrip("#"), 40)
        for value in (manifest.get("search_terms") or [])
        if _clean(str(value).lstrip("#"), 40)
    ]

    title_candidates = [
        hook,
        topic,
        f"{topic}: what happened",
        f"{topic}: the key moment",
        f"What changed in {topic}",
    ]
    title_candidates = list(dict.fromkeys(x for x in title_candidates if x))
    title_candidates.sort(key=lambda x: (
        1 if hook and x == hook else 0,
        len(set(_tokens(x)) & set(_tokens(topic))),
        -abs(len(x) - 55),
    ), reverse=True)

    scene_descriptions = []
    for scene in scenes[:5]:
        description = _clean(
            str(scene.get("description") or scene.get("transcript") or ""),
            180,
        )
        if description:
            scene_descriptions.append(description)

    description_parts = [hook or topic]
    if overlays:
        description_parts.append("Key on-screen beats: " + "; ".join(overlays[:4]))
    if purposes:
        description_parts.append("Edit structure: " + " → ".join(purposes[:6]))
    if scene_descriptions:
        description_parts.append("Source evidence: " + " ".join(scene_descriptions[:2]))
    source_creator = _clean(str((source_manifest or {}).get("creator", "")), 120)
    source_title = _clean(str((source_manifest or {}).get("title", "")), 160)
    if source_creator or source_title:
        description_parts.append(
            "Source: " + " — ".join(x for x in (source_title, source_creator) if x)
        )
    description = _clean("\n\n".join(description_parts), 5000)

    raw_terms = [
        *_tokens(" ".join(search_terms)),
        *_tokens(topic),
        *_tokens(" ".join(overlays)),
    ]
    hashtag_tokens = [
        token.replace("'", "").replace("_", "")
        for token in raw_terms
        if token not in {"the", "and", "this", "that", "what", "with", "from"}
    ]
    counts = Counter(token for token in hashtag_tokens if token)
    hashtags = [f"#{term}" for term, _ in counts.most_common(12)][:8]

    focal_scene = scenes[0] if scenes else {}
    focal = _clean(
        str(focal_scene.get("description") or focal_scene.get("objects") or "the strongest source frame"),
        160,
    )
    thumbnail = (
        f"Use the strongest finished-video frame representing {focal}; "
        f"pair it with the actual hook '{hook or topic}' in 2-5 readable words."
    )

    return {
        "schema_version": "1.0.0",
        "source": "final_content_manifest",
        "title_candidates": title_candidates[:10],
        "selected_title": title_candidates[0] if title_candidates else topic,
        "description": description,
        "hashtags": hashtags,
        "thumbnail_concept": thumbnail,
        "evidence": {
            "source_sha256": str(manifest.get("source_sha256", "")),
            "scene_count": len(scenes),
            "overlay_count": len(overlays),
            "purpose_count": len(purposes),
        },
    }


__all__ = ["generate_final_metadata"]
