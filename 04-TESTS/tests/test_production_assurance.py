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
        provenance={
            "assets": [{"asset_id": "source", "rights_status": "owned"}],
            "final_video": {"rights_status": "owned", "sha256": "a" * 64},
            "run_context": {"platform": "youtube_shorts"},
            "rights_gate": {"status": "cleared", "publish_blocked": False},
        },
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


def test_release_evidence_rejects_blocked_rights_gate(tmp_path: Path):
    evidence = build_release_evidence(
        package_dir=tmp_path,
        readiness={"state": "UPLOAD_PACKAGE_VALID"},
        media_health={"ok": True},
        provenance={
            "assets": [{"asset_id": "source", "rights_status": "owned"}],
            "final_video": {"rights_status": "owned", "sha256": "a" * 64},
            "run_context": {"platform": "youtube_shorts"},
            "rights_gate": {"status": "review_required", "publish_blocked": True, "checked": [{"errors": ["missing declaration"]}]},
        },
        environment={"ok": True},
        artifact_integrity={"ok": True},
    )
    assert evidence["release_candidate"] is False
    assert evidence["checks"]["rights_gate_valid"] is False
    assert evidence["checks"]["rights_clear"] is False


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


def test_human_acceptance_verifier_bootstraps_source_checkout():
    import runpy
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "06-CONFIG-AND-DEPLOYMENT" / "verify_human_acceptance.py"
    main_code = str(Path(__file__).resolve().parents[2] / "01-MAIN-CODE")
    old_path = list(sys.path)
    try:
        while main_code in sys.path:
            sys.path.remove(main_code)
        namespace = runpy.run_path(str(script), run_name="aivf.acceptance_test")
        assert callable(namespace["main"])
        assert str(script.parents[1] / "01-MAIN-CODE") in sys.path
    finally:
        sys.path[:] = old_path


def test_manifest_rejects_wrong_package_and_unsafe_paths(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    target = package / "final.v3.mp4"
    target.write_bytes(b"video")
    manifest = build_artifact_manifest(package, required_files=("final.v3.mp4",))

    wrong_package = dict(manifest)
    wrong_package["package"] = "other-package"
    assert verify_artifact_manifest(package, wrong_package)["ok"] is False

    unsafe = dict(manifest)
    unsafe["files"] = [{"path": "../final.v3.mp4", "size_bytes": 5, "sha256": "0" * 64}]
    assert verify_artifact_manifest(package, unsafe)["ok"] is False


def test_release_evidence_missing_rights_status_fails_closed(tmp_path: Path):
    evidence = build_release_evidence(
        package_dir=tmp_path,
        readiness={"state": "UPLOAD_PACKAGE_VALID"},
        media_health={"ok": True},
        provenance={
            "assets": [{"asset_id": "source"}],
            "final_video": {"rights_status": "owned", "sha256": "a" * 64},
            "run_context": {"platform": "youtube_shorts"},
        },
        environment={"ok": True},
        artifact_integrity={"ok": True},
    )
    assert evidence["release_candidate"] is False
    assert evidence["checks"]["rights_clear"] is False


def test_release_evidence_requires_explicit_rights_gate(tmp_path: Path):
    evidence = build_release_evidence(
        package_dir=tmp_path,
        readiness={"state": "UPLOAD_PACKAGE_VALID"},
        media_health={"ok": True},
        provenance={
            "assets": [{"asset_id": "source", "rights_status": "cc_license"}],
            "final_video": {"rights_status": "owned", "sha256": "a" * 64},
            "run_context": {"platform": "youtube_shorts"},
        },
        environment={"ok": True},
        artifact_integrity={"ok": True},
    )
    assert evidence["release_candidate"] is False
    assert evidence["checks"]["rights_clear"] is False


def test_manifest_detects_readiness_tamper(tmp_path: Path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "final.v3.mp4").write_bytes(b"video")
    (package / "v3_readiness.json").write_text('{"state":"UPLOAD_PACKAGE_VALID"}', encoding="utf-8")
    manifest = build_artifact_manifest(package, required_files=("final.v3.mp4", "v3_readiness.json"))
    (package / "v3_readiness.json").write_text('{"state":"PUBLISH_READY"}', encoding="utf-8")
    verified = verify_artifact_manifest(package, manifest)
    assert verified["ok"] is False
    assert any("hash mismatch: v3_readiness.json" in error for error in verified["errors"])
