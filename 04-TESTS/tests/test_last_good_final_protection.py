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
