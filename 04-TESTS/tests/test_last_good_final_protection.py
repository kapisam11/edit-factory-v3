from __future__ import annotations

import json
from pathlib import Path

from ai_video_factory import v3_stage_pipeline
from ai_video_factory.v3_quality import RenderContractError


def _request(source: Path, package: Path) -> v3_stage_pipeline.V3Request:
    return v3_stage_pipeline.V3Request(
        input_video=str(source),
        topic="last good final regression",
        package_dir=str(package),
        target_seconds=8.0,
        platform="youtube_shorts",
        audience="general short-form viewers",
        bpm=120,
    )


def _seed_compatible_identity(source: Path, package: Path) -> v3_stage_pipeline.V3Request:
    """Represent a previous artifact owned by the same logical V3 job."""
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


def test_failed_rerun_restores_previous_final_v3_file(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    package = tmp_path / "package"
    package.mkdir()
    final = package / "final.v3.mp4"
    final.write_bytes(b"previous-known-good-final")
    request = _seed_compatible_identity(source, package)

    class SeedThenFailStage:
        name = "injected_failure"

        def run(self, context):
            (context.package / "final.v3.mp4").write_bytes(b"corrupt replacement")
            raise RenderContractError("injected render failure")

    result = v3_stage_pipeline.V3PipelineRunner(
        request,
        stages=(SeedThenFailStage(),),
    ).run()

    assert result.errors
    assert result.final_video is None
    assert final.read_bytes() == b"previous-known-good-final"
    assert result.artifacts["last_good_final_preserved"] == str(final)
    assert any("previous final.v3.mp4 was restored" in item for item in result.warnings)
    assert not list(tmp_path.glob(".aivf-v3-last-good-*"))


def test_failed_first_render_does_not_leave_fake_canonical_final(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    package = tmp_path / "package"

    class FalseSuccessStage:
        name = "false_success"

        def run(self, context):
            target = context.package / "final.v3.mp4"
            target.write_bytes(b"not a playable video")
            context.result.final_video = str(target)

    monkeypatch.setattr(
        v3_stage_pipeline,
        "probe_media",
        lambda _path: (_ for _ in ()).throw(RenderContractError("invalid media fixture")),
    )
    result = v3_stage_pipeline.V3PipelineRunner(
        _request(source, package),
        stages=(FalseSuccessStage(),),
    ).run()

    assert result.errors
    assert result.final_video is None
    assert not (package / "final.v3.mp4").exists()
    assert result.artifacts["failed_final_removed"] == str(package / "final.v3.mp4")
    assert (package / "v3_failure.json").is_file()


def test_successful_rerun_replaces_last_good_final_and_removes_snapshot(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    package = tmp_path / "package"
    package.mkdir()
    final = package / "final.v3.mp4"
    final.write_bytes(b"previous final")
    request = _seed_compatible_identity(source, package)

    class SuccessfulStage:
        name = "successful_render"

        def run(self, context):
            target = context.package / "final.v3.mp4"
            target.write_bytes(b"new playable final")
            context.result.final_video = str(target)

    monkeypatch.setattr(
        v3_stage_pipeline,
        "probe_media",
        lambda _path: {"duration": 8.0, "width": 1080, "height": 1920, "has_video": True},
    )
    result = v3_stage_pipeline.V3PipelineRunner(
        request,
        stages=(SuccessfulStage(),),
    ).run()

    assert result.errors == []
    assert result.final_video == str(final)
    assert final.read_bytes() == b"new playable final"
    assert not list(tmp_path.glob(".aivf-v3-last-good-*"))

def test_failed_final_restore_retains_emergency_snapshot(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    package = tmp_path / "package"
    package.mkdir()
    final = package / "final.v3.mp4"
    final.write_bytes(b"previous-known-good-final")
    request = _seed_compatible_identity(source, package)

    class SeedThenFailStage:
        name = "injected_failure"

        def run(self, context):
            (context.package / "final.v3.mp4").write_bytes(b"corrupt replacement")
            raise RenderContractError("injected render failure")

    original_replace = v3_stage_pipeline.os.replace

    def fail_only_last_good_restore(source_path, destination_path):
        source_item = Path(source_path)
        destination_item = Path(destination_path)
        if (
            destination_item == final
            and source_item.name.startswith(".aivf-v3-last-good-")
        ):
            raise OSError("injected restore disk failure")
        return original_replace(source_path, destination_path)

    monkeypatch.setattr(v3_stage_pipeline.os, "replace", fail_only_last_good_restore)
    result = v3_stage_pipeline.V3PipelineRunner(
        _request(source, package),
        stages=(SeedThenFailStage(),),
    ).run()

    assert result.errors
    snapshot = Path(result.artifacts["last_good_snapshot"])
    assert snapshot.is_file()
    assert snapshot.read_bytes() == b"previous-known-good-final"
    assert any("failed to restore the previous final video" in error for error in result.errors)



def test_hard_crash_recovery_restores_durable_snapshot_and_marks_job_failed(monkeypatch, tmp_path):
    """Simulate power loss after workspace reset, when Python recovery handlers cannot run."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    package = tmp_path / "package"
    package.mkdir()
    final = package / "final.v3.mp4"
    final.write_bytes(b"previous-known-good-final")
    request = _seed_compatible_identity(source, package)

    monkeypatch.setattr(
        v3_stage_pipeline,
        "probe_media",
        lambda _path: {
            "duration": 8.0,
            "width": 1080,
            "height": 1920,
            "fps": "30/1",
            "has_video": True,
        },
    )
    import ai_video_factory.v3_quality as quality
    monkeypatch.setattr(quality, "probe_media", v3_stage_pipeline.probe_media)
    monkeypatch.setattr(quality, "_verify_full_decode", lambda _path: None)

    snapshot = v3_stage_pipeline._snapshot_last_good_final(package)
    assert snapshot is not None
    manifest = snapshot.with_name(snapshot.name + ".json")
    assert snapshot.is_file() and manifest.is_file()

    # This represents abrupt termination after the canonical file was removed by reset.
    # No exception handler or finally block is invoked between these filesystem states.
    final.unlink()

    class MustNotStartUntilRecoveryIsReported:
        name = "must_not_start"

        def run(self, _context):
            raise AssertionError("recovery should return a failed result before another render starts")

    result = v3_stage_pipeline.V3PipelineRunner(
        request,
        stages=(MustNotStartUntilRecoveryIsReported(),),
    ).run()

    assert result.errors
    assert result.final_video is None
    assert final.read_bytes() == b"previous-known-good-final"
    assert result.artifacts["last_good_final_preserved"] == str(final)
    assert any("interrupted V3 render was detected" in error for error in result.errors)
    readiness = json.loads((package / "v3_readiness.json").read_text(encoding="utf-8"))
    assert readiness["state"] == "FAILED"
    assert readiness["checks"]["PUBLISH_READY"] is False
    assert not snapshot.exists()
    assert not manifest.exists()


def test_hard_crash_recovery_never_rolls_back_a_verified_newer_success(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    package = tmp_path / "package"
    package.mkdir()
    final = package / "final.v3.mp4"
    final.write_bytes(b"older final")
    request = _seed_compatible_identity(source, package)

    readiness = {
        "state": "UPLOAD_PACKAGE_VALID",
        "checks": {
            "MEDIA_VALID": True,
            "MEDIA_CONTRACT_VALID": True,
            "UPLOAD_PACKAGE_VALID": True,
            "PUBLISH_READY": True,
        },
        "errors": [],
        "warnings": [],
    }
    (package / "v3_readiness.json").write_text(
        json.dumps(readiness), encoding="utf-8"
    )
    (package / "environment_fingerprint.json").write_text(
        json.dumps(
            {
                "target_seconds": 8.0,
                "platform_profile": {"width": 1080, "height": 1920},
            }
        ),
        encoding="utf-8",
    )
    from ai_video_factory.production_assurance import build_artifact_manifest

    manifest = build_artifact_manifest(
        package,
        required_files=(
            "final.v3.mp4",
            "job_identity.json",
            "v3_readiness.json",
            "environment_fingerprint.json",
        ),
    )
    (package / "artifact_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    import ai_video_factory.v3_quality as quality
    media_probe = lambda _path: {
        "duration": 8.0,
        "width": 1080,
        "height": 1920,
        "fps": "30/1",
        "has_video": True,
    }
    monkeypatch.setattr(v3_stage_pipeline, "probe_media", media_probe)
    monkeypatch.setattr(quality, "probe_media", media_probe)
    monkeypatch.setattr(quality, "_verify_full_decode", lambda _path: None)

    snapshot = v3_stage_pipeline._snapshot_last_good_final(package)
    assert snapshot is not None
    final.write_bytes(b"newer verified final")
    # Simulate the new publish having completed and its integrity manifest being
    # atomically refreshed, followed by a crash before stale-snapshot cleanup.
    updated_manifest = build_artifact_manifest(
        package,
        required_files=(
            "final.v3.mp4",
            "job_identity.json",
            "v3_readiness.json",
            "environment_fingerprint.json",
        ),
    )
    (package / "artifact_manifest.json").write_text(
        json.dumps(updated_manifest), encoding="utf-8"
    )

    class MustNotRenderAgain:
        name = "must_not_render_again"

        def run(self, _context):
            raise AssertionError("a verified completed package should be reused")

    result = v3_stage_pipeline.V3PipelineRunner(
        request,
        stages=(MustNotRenderAgain(),),
    ).run()

    assert result.errors == []
    assert result.final_video == str(final)
    assert final.read_bytes() == b"newer verified final"
    assert not snapshot.exists()
    assert not snapshot.with_name(snapshot.name + ".json").exists()


def test_legacy_orphaned_snapshot_is_recovered_after_hard_crash(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source fixture")
    package = tmp_path / "package"
    package.mkdir()
    final = package / "final.v3.mp4"
    final.write_bytes(b"partial replacement")
    _seed_compatible_identity(source, package)

    legacy_snapshot = tmp_path / ".aivf-v3-last-good-legacy.mp4"
    legacy_snapshot.write_bytes(b"legacy previous final")
    monkeypatch.setattr(
        v3_stage_pipeline,
        "_probe_and_decode_video",
        lambda path, **_kwargs: Path(path) == legacy_snapshot,
    )

    result = v3_stage_pipeline.V3PipelineRunner(
        _request(source, package),
        stages=(),
    ).run()

    assert result.errors
    assert result.final_video is None
    assert final.read_bytes() == b"legacy previous final"
    assert result.artifacts["last_good_final_preserved"] == str(final)
    assert not legacy_snapshot.exists()
