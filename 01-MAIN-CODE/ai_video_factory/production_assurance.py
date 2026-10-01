"""Production assurance contracts for deterministic package integrity and release evidence.

This module is deliberately dependency-light so it can run in the dashboard, CLI,
CI, and isolated wheel without optional media/AI dependencies.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
_DEFAULT_EXCLUDES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "artifact_manifest.json",
    "release_evidence.json",
    "resource_usage.json",
    # Created outside the final integrity snapshot or intentionally transient.
    "v3_job_result.json",
    "final.v3.retention.mp4",
}


def canonical_json(value: Any) -> str:
    """Return deterministic JSON for fingerprints and manifests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint_mapping(value: Mapping[str, Any], *, length: int = 32) -> str:
    digest = hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()
    return digest[: max(16, int(length))]


def file_sha256(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(max(4096, int(chunk_size))), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_files(
    root: Path,
    *,
    excludes: Iterable[str] = _DEFAULT_EXCLUDES,
) -> list[Path]:
    excluded = {str(item) for item in excludes}
    files: list[Path] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in excluded and not (Path(current) / name).is_symlink()]
        for name in names:
            if name in excluded:
                continue
            candidate = Path(current) / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            files.append(candidate)
    files.sort(key=lambda item: item.relative_to(root).as_posix())
    return files


def build_artifact_manifest(
    package_dir: str | Path,
    *,
    required_files: Iterable[str] = (),
    include_hashes: bool = True,
) -> dict[str, Any]:
    """Create a deterministic inventory of regular files in a package directory."""
    root = Path(package_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)

    entries: list[dict[str, Any]] = []
    for path in _relative_files(root):
        rel = path.relative_to(root).as_posix()
        stat = path.stat()
        item: dict[str, Any] = {
            "path": rel,
            "size_bytes": int(stat.st_size),
        }
        if include_hashes:
            item["sha256"] = file_sha256(path)
        entries.append(item)

    required = [str(item).replace("\\", "/") for item in required_files]
    present = {item["path"] for item in entries}
    missing = sorted(path for path in required if path not in present)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "package": root.name,
        "file_count": len(entries),
        "files": entries,
        "required_files": required,
        "missing_required_files": missing,
        "ok": not missing,
    }
    manifest["manifest_sha256"] = hashlib.sha256(
        canonical_json({key: value for key, value in manifest.items() if key != "manifest_sha256"}).encode("utf-8")
    ).hexdigest()
    return manifest


def _safe_manifest_relative_path(value: object) -> str:
    """Normalize and validate manifest paths so integrity checks cannot escape the package."""
    rel = str(value or "").replace("\\", "/")
    path = Path(rel)
    if not rel or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe manifest path: {rel!r}")
    return path.as_posix()


def verify_artifact_manifest(
    package_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    verify_hashes: bool = True,
) -> dict[str, Any]:
    """Verify an existing artifact manifest against the current package contents."""
    root = Path(package_dir).resolve()
    errors: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported manifest schema")
    if str(manifest.get("package") or "") != root.name:
        errors.append("manifest package name mismatch")
    supplied_manifest_hash = str(manifest.get("manifest_sha256") or "")
    if supplied_manifest_hash:
        canonical = {
            key: value for key, value in manifest.items()
            if key != "manifest_sha256"
        }
        expected_manifest_hash = hashlib.sha256(
            canonical_json(canonical).encode("utf-8")
        ).hexdigest()
        if supplied_manifest_hash != expected_manifest_hash:
            errors.append("manifest hash mismatch")
    else:
        errors.append("manifest_sha256 is missing")
    files = manifest.get("files") or []
    if not isinstance(files, list):
        return {"ok": False, "errors": ["manifest.files must be a list"]}

    expected_paths = []
    for raw in files:
        if not isinstance(raw, Mapping):
            errors.append("manifest contains a non-object file entry")
            continue
        try:
            rel = _safe_manifest_relative_path(raw.get("path"))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        expected_paths.append(rel)
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():
            errors.append(f"missing artifact: {rel}")
            continue
        size = raw.get("size_bytes")
        if size is not None and int(size) != target.stat().st_size:
            errors.append(f"size mismatch: {rel}")
        if verify_hashes and raw.get("sha256") and file_sha256(target) != str(raw["sha256"]):
            errors.append(f"hash mismatch: {rel}")

    actual = {path.relative_to(root).as_posix() for path in _relative_files(root)}
    unexpected = sorted(actual - set(expected_paths))
    missing = sorted(set(expected_paths) - actual)
    if unexpected:
        errors.append("unexpected package files present: " + ", ".join(unexpected[:20]))
    if missing:
        errors.append("manifest files missing from package: " + ", ".join(missing[:20]))
    required: list[str] = []
    for raw in (manifest.get("required_files") or []):
        try:
            required.append(_safe_manifest_relative_path(raw))
        except ValueError as exc:
            errors.append(str(exc))
    for rel in required:
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():
            errors.append(f"required artifact missing: {rel}")

    return {"ok": not errors, "errors": errors, "checked_files": len(expected_paths)}


def build_environment_fingerprint(
    *,
    pipeline_version: str,
    platform_name: str,
    target_seconds: float,
    platform_profile: Mapping[str, Any],
) -> dict[str, Any]:
    payload = {
        "pipeline_version": str(pipeline_version),
        "python": platform.python_version(),
        "platform": platform_name,
        "target_seconds": round(float(target_seconds), 6),
        "platform_profile": dict(platform_profile),
        "schema_version": SCHEMA_VERSION,
    }
    payload["fingerprint"] = fingerprint_mapping(payload)
    return payload


def build_release_evidence(
    *,
    package_dir: str | Path,
    readiness: Mapping[str, Any],
    media_health: Mapping[str, Any],
    provenance: Mapping[str, Any],
    environment: Mapping[str, Any],
    artifact_integrity: Mapping[str, Any],
) -> dict[str, Any]:
    assets = provenance.get("assets")
    final_video = provenance.get("final_video")
    final_video_record = final_video if isinstance(final_video, Mapping) else {}
    rights_gate = provenance.get("rights_gate")
    rights_gate_ok = (
        isinstance(rights_gate, Mapping)
        and str(rights_gate.get("status") or "").strip().lower() == "cleared"
        and not bool(rights_gate.get("publish_blocked"))
        and not any(
            isinstance(item, Mapping) and bool(item.get("errors"))
            for item in (rights_gate.get("checked") or [])
        )
    )
    cleared_rights = {"owned", "explicit_permission", "commercial_license", "public_domain", "cc_license"}
    provenance_present = (
        isinstance(assets, list)
        and isinstance(final_video, Mapping)
        and bool(final_video.get("sha256"))
        and isinstance(provenance.get("run_context"), Mapping)
    )
    rights_records = [item for item in (assets or []) if isinstance(item, Mapping)]
    rights_clear = (
        provenance_present
        and len(rights_records) == len(assets or [])
        and bool(rights_records)
        and rights_gate_ok
        and str(final_video_record.get("rights_status") or "").strip().lower() in cleared_rights
        and all(str(item.get("rights_status") or "").strip().lower() in cleared_rights for item in rights_records)
    )
    checks = {
        "readiness_valid": readiness.get("state") == "UPLOAD_PACKAGE_VALID",
        "media_valid": bool(media_health.get("ok")),
        "provenance_present": provenance_present,
        "rights_clear": rights_clear,
        "environment_valid": bool(environment.get("ok")),
        "artifact_integrity_valid": bool(artifact_integrity.get("ok")),
        "rights_gate_valid": rights_gate_ok,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "package": Path(package_dir).name,
        "checks": checks,
        "release_candidate": all(checks.values()),
        "human_review_required": True,
        "human_review_reason": "Automated checks cannot establish artistic quality or legal rights.",
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
    }


__all__ = [
    "SCHEMA_VERSION",
    "canonical_json",
    "fingerprint_mapping",
    "file_sha256",
    "build_artifact_manifest",
    "verify_artifact_manifest",
    "build_environment_fingerprint",
    "build_release_evidence",
]
