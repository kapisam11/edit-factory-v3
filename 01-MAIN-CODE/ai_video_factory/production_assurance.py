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


def verify_artifact_manifest(
    package_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    verify_hashes: bool = True,
) -> dict[str, Any]:
    """Verify an existing artifact manifest against the current package contents."""
    root = Path(package_dir).resolve()
    errors: list[str] = []
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
        rel = str(raw.get("path") or "").replace("\\", "/")
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
    required = [str(item) for item in (manifest.get("required_files") or [])]
    for rel in required:
        if not (root / rel).is_file():
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
) -> dict[str, Any]:
    checks = {
        "readiness_valid": readiness.get("state") == "UPLOAD_PACKAGE_VALID",
        "media_valid": bool(media_health.get("ok")),
        "provenance_present": isinstance(provenance.get("assets"), list),
        "rights_clear": not any(
            str(item.get("rights_status") or "").lower() in {"review_required", "unverified"}
            for item in (provenance.get("assets") or [])
            if isinstance(item, Mapping)
        ) and str((provenance.get("final_video") or {}).get("rights_status") or "").lower()
        not in {"review_required", "unverified"},
        "environment_valid": bool(environment.get("ok")),
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
