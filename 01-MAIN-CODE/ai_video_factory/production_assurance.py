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
    required_raw = manifest.get("required_files")
    if required_raw is None:
        required_raw = []
    elif not isinstance(required_raw, list):
        errors.append("manifest.required_files must be a list")
        required_raw = []
    for raw in required_raw:
        try:
            required.append(_safe_manifest_relative_path(raw))
        except ValueError as exc:
            errors.append(str(exc))
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
        manifest = json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "errors": [f"release evidence inputs are unreadable: {exc}"]}

    loaded_inputs = {
        "readiness": readiness,
        "provenance": provenance,
        "media_health": media_health,
        "diagnostics": diagnostics,
        "environment_fingerprint": environment_fingerprint,
        "manifest": manifest,
    }
    invalid_inputs = [name for name, value in loaded_inputs.items() if not isinstance(value, Mapping)]
    if invalid_inputs:
        return {
            "ok": False,
            "errors": [
                "release evidence input must be a JSON object: " + ", ".join(invalid_inputs)
            ],
        }
    environment = {**diagnostics, **environment_fingerprint}

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