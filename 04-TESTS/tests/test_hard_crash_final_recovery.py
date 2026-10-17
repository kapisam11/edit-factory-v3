from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ai_video_factory import v3_stage_pipeline


def _request(source: Path, package: Path) -> v3_stage_pipeline.V3Request:
    return v3_stage_pipeline.V3Request(
        input_video=str(source),
        topic="hard crash durable recovery",
        package_dir=str(package),
        target_seconds=8.0,
        platform="youtube_shorts",
        audience="general short-form viewers",
        bpm=120,
    )


def _seed_compatible_identity(source: Path, package: Path) -> v3_stage_pipeline.V3Request:
    request = _request(source, package)
    identity = v3_stage_pipeline._expected_job_identity(request)
    blueprint_hash = "test-blueprint-hash"
    identity["blueprint_hash"] = blueprint_hash
    identity["job_id"] = v3_stage_pipeline.job_identity(
        source_hash=identity["source_hash"],
        blueprint_hash=blueprint_hash,
        config_hash=identity["configuration_hash"],
        renderer_version=identity["renderer_version"],
        platform_policy_version=identity["platform_policy_version"],
    )
    (package / "job_identity.json").write_text(
        json.dumps(identity), encoding="utf-8"
    )
    return request


def test_hard_process_exit_restores_durable_last_good_final_on_next_run(tmp_path):
    """Prove recovery across an actual process boundary, not just a mocked exception."""
    repo_root = Path(__file__).resolve().parents[2]
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    package = tmp_path / "package"
    package.mkdir()
    final = package / "final.v3.mp4"
    final.write_bytes(b"previous-known-good-final")
    request = _seed_compatible_identity(source, package)
    snapshot, manifest, partial = v3_stage_pipeline._last_good_snapshot_paths(package)

    # Run the snapshot-and-interruption sequence in a child interpreter. os._exit()
    # deliberately bypasses exception handlers, finally blocks, and interpreter cleanup,
    # representing a worker/process crash after destructive workspace reset has started.
    child_script = r"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from ai_video_factory import v3_stage_pipeline

package = Path(sys.argv[1])
# This is a deterministic test of the crash window, not a test of host disk capacity.
v3_stage_pipeline.shutil.disk_usage = lambda _path: SimpleNamespace(free=1024**3)
snapshot = v3_stage_pipeline._snapshot_last_good_final(package)
if snapshot is None:
    os._exit(90)
(package / "final.v3.mp4").unlink()
os._exit(73)
"""
    env = os.environ.copy()
    source_root = str(repo_root / "01-MAIN-CODE")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_root, env.get("PYTHONPATH", "")) if part
    )
    crashed = subprocess.run(
        [sys.executable, "-c", child_script, str(package)],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert crashed.returncode == 73, (
        "child did not exit at the injected crash point; "
        f"returncode={crashed.returncode}, stderr={crashed.stderr[-2000:]}"
    )
    assert not final.exists(), "fixture must represent the crash after final removal"
    assert snapshot.is_file()
    assert manifest.is_file()
    assert not partial.exists()

    stage_calls: list[bool] = []

    class MustNotRenderBeforeRecoveryIsReported:
        name = "must_not_render_before_recovery"

        def run(self, _context):
            stage_calls.append(True)
            raise AssertionError("a new render must not start during crash recovery")

    result = v3_stage_pipeline.V3PipelineRunner(
        request,
        stages=(MustNotRenderBeforeRecoveryIsReported(),),
    ).run()

    assert stage_calls == []
    assert result.errors
    assert result.final_video is None
    assert final.read_bytes() == b"previous-known-good-final"
    assert result.artifacts["last_good_final_preserved"] == str(final)
    assert any("interrupted V3 render was detected" in error for error in result.errors)

    readiness = json.loads(
        (package / "v3_readiness.json").read_text(encoding="utf-8")
    )
    assert readiness["state"] == "FAILED"
    assert readiness["checks"]["PUBLISH_READY"] is False
    assert not snapshot.exists()
    assert not manifest.exists()
    assert not partial.exists()
