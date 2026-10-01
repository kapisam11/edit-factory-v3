import json
import subprocess
import sys
from pathlib import Path

from ai_video_factory.production_assurance import build_artifact_manifest


def test_human_acceptance_verifier_matches_manifest_and_explicit_review(tmp_path):
    package = tmp_path / "package-1"
    package.mkdir()
    (package / "final.v3.mp4").write_bytes(b"video")
    manifest = build_artifact_manifest(package, required_files=("final.v3.mp4",))
    (package / "artifact_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

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
