from pathlib import Path

from ai_video_factory.production_assurance import (
    build_artifact_manifest,
    build_environment_fingerprint,
    build_release_evidence,
    fingerprint_mapping,
    verify_artifact_manifest,
)


def test_artifact_manifest_is_deterministic_and_excludes_integrity_files(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "final.v3.mp4").write_bytes(b"video")
    (package / "metadata.json").write_text("{\"ok\":true}\n", encoding="utf-8")

    manifest = build_artifact_manifest(
        package,
        required_files=("final.v3.mp4", "metadata.json"),
    )
    assert manifest["ok"] is True
    assert manifest["manifest_sha256"]
    assert all(item["path"] != "artifact_manifest.json" for item in manifest["files"])

    (package / "artifact_manifest.json").write_text("integrity wrapper", encoding="utf-8")
    verified = verify_artifact_manifest(package, manifest)
    assert verified["ok"] is True


def test_artifact_manifest_detects_tampering(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    target = package / "final.v3.mp4"
    target.write_bytes(b"original")

    manifest = build_artifact_manifest(package, required_files=("final.v3.mp4",))
    target.write_bytes(b"tampered")
    verified = verify_artifact_manifest(package, manifest)
    assert verified["ok"] is False
    assert any("hash mismatch" in error for error in verified["errors"])


def test_environment_and_release_evidence_are_explicit_about_human_review():
    environment = build_environment_fingerprint(
        pipeline_version="3.0.0",
        platform_name="youtube_shorts",
        target_seconds=8.0,
        platform_profile={"width": 1080, "height": 1920},
    )
    assert len(environment["fingerprint"]) >= 16
    assert fingerprint_mapping({"a": 1, "b": 2}) == fingerprint_mapping({"b": 2, "a": 1})

    evidence = build_release_evidence(
        package_dir="/tmp/package",
        readiness={"state": "UPLOAD_PACKAGE_VALID"},
        media_health={"ok": True},
        provenance={"assets": []},
        environment={"ok": True},
        artifact_integrity={"ok": True},
    )
    assert evidence["release_candidate"] is True
    assert evidence["human_review_required"] is True


def test_manifest_self_hash_and_rights_gate_prevent_false_green(tmp_path):
    from ai_video_factory.production_assurance import build_release_evidence

    package = tmp_path / "package"
    package.mkdir()
    (package / "final.v3.mp4").write_bytes(b"video")
    manifest = build_artifact_manifest(package, required_files=("final.v3.mp4",))

    tampered = dict(manifest)
    tampered["manifest_sha256"] = "0" * 64
    assert verify_artifact_manifest(package, tampered)["ok"] is False

    blocked = build_release_evidence(
        package_dir=package,
        readiness={"state": "UPLOAD_PACKAGE_VALID"},
        media_health={"ok": True},
        provenance={"assets": [{"rights_status": "review_required"}]},
        environment={"ok": True},
        artifact_integrity={"ok": True},
    )
    assert blocked["release_candidate"] is False
    assert blocked["checks"]["rights_clear"] is False


def test_release_evidence_requires_artifact_integrity(tmp_path: Path):
    evidence = build_release_evidence(
        package_dir=tmp_path,
        readiness={"state": "UPLOAD_PACKAGE_VALID"},
        media_health={"ok": True},
        provenance={"assets": []},
        environment={"ok": True},
        artifact_integrity={"ok": False, "errors": ["hash mismatch"]},
    )
    assert evidence["release_candidate"] is False
    assert evidence["checks"]["artifact_integrity_valid"] is False
