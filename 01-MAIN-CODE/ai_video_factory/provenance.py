"""Asset provenance and rights metadata; never infers legal permission."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .production_guardrails import atomic_write_json, sha256_file

RIGHTS_STATUSES = {
    "owned", "explicit_permission", "commercial_license", "public_domain",
    "cc_license", "license_identified", "unverified", "review_required",
}

def normalize_rights_status(value: str | None) -> str:
    candidate = str(value or "review_required").strip().lower()
    return candidate if candidate in RIGHTS_STATUSES else "review_required"

def build_asset_record(
    path: str | Path,
    *,
    asset_id: str,
    source: str,
    source_url: str = "",
    rights_status: str = "review_required",
    license_name: str = "",
    license_url: str = "",
    attribution: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    record: dict[str, Any] = {
        "asset_id": str(asset_id),
        "path": target.name,
        "source": str(source or "unknown"),
        "source_url": str(source_url or ""),
        "rights_status": normalize_rights_status(rights_status),
        "license": {
            "name": str(license_name or "unknown").strip() or "unknown",
            "url": str(license_url or "").strip(),
        },
        "attribution": str(attribution or ""),
        "sha256": sha256_file(target),
        "size_bytes": target.stat().st_size,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        record["extra"] = dict(extra)
    return record

def add_asset(manifest: dict[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    assets = manifest.setdefault("assets", [])
    if not isinstance(assets, list):
        raise ValueError("manifest.assets must be a list")
    asset_id = str(record.get("asset_id") or "")
    if not asset_id:
        raise ValueError("asset_id is required")
    assets[:] = [item for item in assets if str(item.get("asset_id")) != asset_id]
    assets.append(dict(record))
    return manifest

def provenance_manifest(
    package_dir: str | Path,
    *,
    final_video: str | Path | None = None,
    assets: list[Mapping[str, Any]] | None = None,
    pipeline_version: str = "3.0.0",
    run_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    package = Path(package_dir)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "pipeline_version": pipeline_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "package": package.name,
        "assets": [dict(item) for item in (assets or [])],
        "final_video": None,
        "run_context": dict(run_context or {}),
    }
    if final_video and Path(final_video).is_file():
        manifest["final_video"] = build_asset_record(
            final_video,
            asset_id="final_video",
            source="edit_factory",
            rights_status="owned",
        )
    return manifest

def write_provenance(path: str | Path, manifest: Mapping[str, Any]) -> str:
    return atomic_write_json(path, dict(manifest))

def load_provenance(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
        raise ValueError("invalid provenance manifest")
    return payload

def manifest_needs_rights_review(manifest: Mapping[str, Any]) -> bool:
    for asset in manifest.get("assets") or []:
        if normalize_rights_status(asset.get("rights_status")) in {"review_required", "unverified"}:
            return True
    final_video = manifest.get("final_video") or {}
    return normalize_rights_status(final_video.get("rights_status")) in {"review_required", "unverified"}

__all__ = [
    "RIGHTS_STATUSES", "normalize_rights_status", "build_asset_record",
    "add_asset", "provenance_manifest", "write_provenance",
    "load_provenance", "manifest_needs_rights_review",
]
