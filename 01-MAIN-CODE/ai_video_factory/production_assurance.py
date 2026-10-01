"""Production assurance contracts for deterministic package integrity and release evidence.

This module is deliberately dependency-light so it can run in the dashboard, CLI,
CI, and isolated wheel without optional media/AI dependencies.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
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
        if not SHA256_RE.fullmatch(supplied_manifest_hash):
            errors.append("manifest_sha256 has invalid format")
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
    if manifest.get("ok") is not True:
        errors.append("manifest ok flag is not true")
    if manifest.get("file_count") != len(files):
        errors.append("manifest file_count does not match files length")

    expected_paths = []
    seen_paths: set[str] = set()
    for raw in files:
        if not isinstance(raw, Mapping):
            errors.append("manifest contains a non-object file entry")
            continue
        try:
            rel = _safe_manifest_relative_path(raw.get("path"))
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if rel in seen_paths:
            errors.append(f"duplicate manifest path: {rel}")
        seen_paths.add(rel)
        expected_paths.append(rel)
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():
            errors.append(f"missing artifact: {rel}")
            continue
        size = raw.get("size_bytes")
        if size is not None:
            try:
                parsed_size = int(size)
            except (TypeError, ValueError):
                errors.append(f"invalid size_bytes: {rel}")
            else:
                if parsed_size < 0:
                    errors.append(f"invalid size_bytes: {rel}")
                elif parsed_size != target.stat().st_size:
                    errors.append(f"size mismatch: {rel}")
        if verify_hashes:
            digest = str(raw.get("sha256") or "")
            if not SHA256_RE.fullmatch(digest):
                errors.append(f"invalid sha256: {rel}")
            elif file_sha256(target) != digest:
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
    if not isinstance(manifest.get("required_files") or [], list):
        errors.append("manifest.required_files must be a list")
    for rel in required:
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():
            errors.append(f"required artifact missing: {rel}")

    return {"ok": not errors, "errors": errors, "checked_files": len(expected_paths)}


def verify_release_evidence(package_dir: str | Path, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Recompute release checks from package contents and compare them with stored evidence."""
    root = Path(package_dir).resolve()
    errors: list[str] = []
    try:
        readiness = json.loads((root / "v3_readiness.json").read_text(encoding="utf-8"))
        provenance = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
        media_health = json.loads((root / "final_media_health.json").read_text(encoding="utf-8"))
        diagnostics = json.loads((root / "diagnostics.json").read_text(encoding="utf-8"))
        environment_fingerprint = json.loads((root / "environment_fingerprint.json").read_text(encoding="utf-8"))
        environment = {**diagnostics, **environment_fingerprint}
        manifest = json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "errors": [f"release evidence inputs are unreadable: {exc}"]}

    integrity = verify_artifact_manifest(root, manifest)
    if not integrity.get("ok"):
        errors.extend(str(error) for error in (integrity.get("errors") or [])[:10])

    expected = build_release_evidence(
        package_dir=root,
        readiness=readiness,
        media_health=media_health,
        provenance=provenance,
        environment=environment,
        artifact_integrity=integrity,
    )
    if evidence.get("schema_version") != expected.get("schema_version"):
        errors.append("release-evidence schema version mismatch")
    if evidence.get("checks") != expected.get("checks"):
        errors.append("stored release-evidence checks do not match recomputed checks")
    if bool(evidence.get("release_candidate")) != bool(expected.get("release_candidate")):
        errors.append("stored release_candidate does not match recomputed release_candidate")
    if evidence.get("human_review_required") is not True:
        errors.append("release-evidence human-review boundary is missing")
    if str(evidence.get("package") or "") != root.name:
        errors.append("release-evidence package name mismatch")
    return {"ok": not errors, "errors": errors, "recomputed": expected}


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
    asset_ids = {
        str(item.get("asset_id") or "")
        for item in (assets or [])
        if isinstance(item, Mapping)
    }
    checked_rights = rights_gate.get("checked") if isinstance(rights_gate, Mapping) else None
    checked_asset_ids = {
        str(item.get("asset_id") or "")
        for item in (checked_rights or [])
        if isinstance(item, Mapping)
    } if isinstance(checked_rights, list) else set()
    rights_gate_ok = (
        isinstance(rights_gate, Mapping)
        and str(rights_gate.get("status") or "").strip().lower() == "cleared"
        and not bool(rights_gate.get("publish_blocked"))
        and isinstance(checked_rights, list)
        and bool(checked_rights)
        and checked_asset_ids == asset_ids
        and len(checked_asset_ids) == len(asset_ids)
        and len(asset_ids) == len(assets or [])
        and all(
            isinstance(item, Mapping)
            and isinstance(item.get("errors"), list)
            and not item.get("errors")
            and str(item.get("rights_basis") or "").strip().lower() in cleared_rights
            and bool(str(item.get("asset_id") or "").strip())
            for item in checked_rights
        )
    )
    cleared_rights = {"owned", "explicit_permission", "commercial_license", "public_domain", "cc_license"}
    asset_records_valid = (
        isinstance(assets, list)
        and all(
            isinstance(item, Mapping)
            and bool(str(item.get("asset_id") or "").strip())
            and bool(SHA256_RE.fullmatch(str(item.get("sha256") or "")))
            for item in assets
        )
    )
    final_hash = str(final_video_record.get("sha256") or "")
    provenance_present = (
        asset_records_valid
        and isinstance(final_video, Mapping)
        and bool(SHA256_RE.fullmatch(final_hash))
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
    readiness_checks = readiness.get("checks") if isinstance(readiness.get("checks"), Mapping) else {}
    readiness_valid = (
        readiness.get("state") == "UPLOAD_PACKAGE_VALID"
        and all(
            readiness_checks.get(key) is True
            for key in ("MEDIA_VALID", "MEDIA_CONTRACT_VALID", "UPLOAD_PACKAGE_VALID")
        )
        and not bool(readiness.get("errors"))
    )
    checks = {
        "readiness_valid": readiness_valid,
        "media_valid": media_health.get("ok") is True,
        "provenance_present": provenance_present,
        "rights_clear": rights_clear,
        "environment_valid": environment.get("ok") is True,
        "artifact_integrity_valid": artifact_integrity.get("ok") is True,
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
    "verify_release_evidence",
]
