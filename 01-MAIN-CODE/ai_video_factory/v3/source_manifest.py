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
    asset_origin: str = "unknown"
    commercial_use: str = "review_required"
    permitted_uses: str = ""
    attribution_required: bool = False
    rights_evidence_url: str = ""
    declared_by: str = ""
    declared_at: str = ""
    human_approval_status: str = "review_required"
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
            "asset_origin": self.asset_origin,
            "commercial_use": self.commercial_use,
            "permitted_uses": self.permitted_uses,
            "attribution_required": self.attribution_required,
            "rights_evidence_url": self.rights_evidence_url,
            "declared_by": self.declared_by,
            "declared_at": self.declared_at,
            "human_approval_status": self.human_approval_status,
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
        asset_origin=str(raw.get("asset_origin") or "unknown").strip().lower(),
        commercial_use=str(raw.get("commercial_use") or "review_required").strip().lower(),
        permitted_uses=str(raw.get("permitted_uses") or "").strip(),
        attribution_required=(
            raw.get("attribution_required") is True
            or str(raw.get("attribution_required") or "").strip().lower() in {"1", "true", "yes", "on"}
        ),
        rights_evidence_url=str(raw.get("evidence_url") or raw.get("rights_evidence_url") or "").strip(),
        declared_by=str(raw.get("declared_by") or "").strip(),
        declared_at=str(raw.get("declared_at") or "").strip(),
        human_approval_status=str(raw.get("human_approval_status") or "review_required").strip().lower(),
        metadata=raw,
    )


__all__ = ["SourceManifest", "build_source_manifest"]
