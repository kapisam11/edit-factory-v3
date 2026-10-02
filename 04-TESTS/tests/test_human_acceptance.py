import json
import subprocess
import sys
from pathlib import Path

from ai_video_factory.production_assurance import build_artifact_manifest, build_release_evidence, verify_artifact_manifest


def test_human_acceptance_verifier_matches_manifest_and_explicit_review(tmp_path):
    package = tmp_path / "package-1"
    package.mkdir()
    final_video = package / "final.v3.mp4"
    final_video.write_bytes(b"video")
    (package / "v3_readiness.json").write_text(json.dumps({
        "state": "UPLOAD_PACKAGE_VALID",
        "checks": {"MEDIA_VALID": True, "MEDIA_CONTRACT_VALID": True, "UPLOAD_PACKAGE_VALID": True},
        "errors": [],
    }), encoding="utf-8")
    digest = __import__("hashlib").sha256(final_video.read_bytes()).hexdigest()
    provenance = {
        "schema_version": 1,
        "assets": [{"asset_id": "source", "path": final_video.name, "rights_status": "owned", "sha256": digest}],
        "final_video": {"asset_id": "final_video", "rights_status": "owned", "sha256": digest},
        "run_context": {"platform": "youtube_shorts"},
        "rights_gate": {
            "status": "cleared",
            "publish_blocked": False,
            "checked": [{"asset_id": "source", "errors": []}],
        },
    }
    (package / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    (package / "final_media_health.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (package / "diagnostics.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (package / "environment_fingerprint.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    manifest = build_artifact_manifest(
        package,
        required_files=(
            "final.v3.mp4",
            "v3_readiness.json",
            "provenance.json",
            "final_media_health.json",
            "diagnostics.json",
            "environment_fingerprint.json",
        ),
    )
    (package / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    integrity = verify_artifact_manifest(package, manifest)
    assert integrity["ok"] is True
    release_evidence = build_release_evidence(
        package_dir=package,
        readiness=json.loads((package / "v3_readiness.json").read_text(encoding="utf-8")),
        media_health={"ok": True},
        provenance=provenance,
        environment={"ok": True},
        artifact_integrity=integrity,
    )
    (package / "release_evidence.json").write_text(json.dumps(release_evidence), encoding="utf-8")

    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text(json.dumps({
        "schema_version": 1,
        "package_name": package.name,
        "artifact_manifest_sha256": manifest["manifest_sha256"],
        "video_reviewed": True,
        "video_acceptance": "accepted",
        "audio_reviewed": True,
        "audio_acceptance": "accepted",
        "rights_reviewed": True,
        "rights_acceptance": "accepted",
        "reviewer": "release operator",
        "reviewed_at": "2026-09-30T18:00:00Z",
    }), encoding="utf-8")

    script = Path("06-CONFIG-AND-DEPLOYMENT/verify_human_acceptance.py")
    result = subprocess.run(
        [sys.executable, str(script), str(package), str(acceptance)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["human_review_complete"] is True
