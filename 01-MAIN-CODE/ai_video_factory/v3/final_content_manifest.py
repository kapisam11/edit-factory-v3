"""Manifest of what the planned/finished edit actually contains."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def build_final_content_manifest(
    *,
    topic: str,
    blueprint: Mapping[str, Any],
    footage_evidence: Mapping[str, Any] | None,
    final_media_metadata: Mapping[str, Any] | None,
    source_manifest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    clip_plan = blueprint.get("clip_plan") or []
    hooks = blueprint.get("hooks") or []
    overlays = [
        str(item.get("text_overlay", "")).strip()
        for item in clip_plan
        if isinstance(item, Mapping) and str(item.get("text_overlay", "")).strip()
    ]
    purposes = [
        str(item.get("purpose", "")).strip()
        for item in clip_plan
        if isinstance(item, Mapping) and str(item.get("purpose", "")).strip()
    ]
    source_scenes = (footage_evidence or {}).get("top_scenes") or []
    search_terms = [str(topic).strip()]
    search_terms.extend(
        str(tag).strip().lstrip("#")
        for tag in blueprint.get("hashtags", [])
        if str(tag).strip()
    )
    return {
        "schema_version": "1.0.0",
        "topic": str(topic).strip(),
        "edit_type": str(blueprint.get("edit_type", "")).strip(),
        "hook": (hooks[0].get("text", "") if hooks and isinstance(hooks[0], Mapping) else ""),
        "hooks": [
            str(item.get("text", "")).strip()
            for item in hooks
            if isinstance(item, Mapping)
        ],
        "overlays": overlays,
        "clip_purposes": purposes,
        "source_scenes": source_scenes[:12],
        "source_sha256": str((source_manifest or {}).get("source_sha256", "")),
        "final_media": dict(final_media_metadata or {}),
        "search_terms": list(dict.fromkeys(term for term in search_terms if term)),
    }


__all__ = ["build_final_content_manifest"]
