"""Typed source manifest kept separate from the creative blueprint."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import hashlib


@dataclass(frozen=True)
class SourceManifest:
    source_path: str
    source_sha256: str
    rights_status: str = "review_required"
    creator: str = ""
    title: str = ""
    url: str = ""
    license_name: str = ""
    license_url: str = ""
    attribution: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "rights_status": self.rights_status,
            "creator": self.creator,
            "title": self.title,
            "url": self.url,
            "license_name": self.license_name,
            "license_url": self.license_url,
            "attribution": self.attribution,
            "metadata": dict(self.metadata),
        }


def build_source_manifest(
    path: str | Path,
    metadata: Mapping[str, Any] | None = None,
) -> SourceManifest:
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    raw = dict(metadata or {})
    return SourceManifest(
        source_path=str(source),
        source_sha256=digest.hexdigest(),
        rights_status=str(raw.get("rights_status") or "review_required").strip().lower(),
        creator=str(raw.get("creator") or "").strip(),
        title=str(raw.get("title") or "").strip(),
        url=str(raw.get("url") or raw.get("source_url") or "").strip(),
        license_name=str(raw.get("license_name") or "").strip(),
        license_url=str(raw.get("license_url") or "").strip(),
        attribution=str(raw.get("attribution") or "").strip(),
        metadata=raw,
    )


__all__ = ["SourceManifest", "build_source_manifest"]
