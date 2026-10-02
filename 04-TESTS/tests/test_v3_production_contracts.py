import json
from pathlib import Path

import pytest

from ai_video_factory import artifact_readiness, render_engine, v3_pipeline, v3_semantic_qc, v3_stage_pipeline
from ai_video_factory.production_models import ProductionResult, Scene
from ai_video_factory.v3_engine import V3Config, create_v3_blueprint


def test_silent_source_voiceover_preserves_video_duration(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    voice = tmp_path / "voice.mp3"
    output = tmp_path / "out.mp4"
    video.write_bytes(b"video")
    voice.write_bytes(b"voice")
    calls = []

    monkeypatch.setattr(
        render_engine,
        "validate_media_output",
        lambda path, **kwargs: {
            "streams": [{"codec_type": "video"}],
            "format": {"duration": 12.5},
        },
    )
    monkeypatch.setattr(render_engine, "run_ffmpeg", lambda command: calls.append(command))

    render_engine.mix_voiceover(str(video), str(voice), str(output))

    command = calls[0]
    assert "-shortest" not in command
    assert command[command.index("-t") + 1] == "12.500"
    assert "apad" in command[command.index("-filter_complex") + 1]


def test_readiness_failure_is_promoted_to_v3_error():
    result = ProductionResult(package_dir="out")
    readiness = artifact_readiness.ReadinessReport(
        "MEDIA_CONTRACT_VALID",
        {
            "MEDIA_VALID": True,
            "MEDIA_CONTRACT_VALID": True,
            "UPLOAD_PACKAGE_VALID": False,
            "PUBLISH_READY": False,
        },
        ["UPLOAD_PACKAGE_VALID: required upload package is incomplete"],
        [],
    )

    v3_pipeline._apply_readiness_contract(result, readiness)

    assert any("required upload package is incomplete" in error for error in result.errors)
    assert result.artifacts["v3_readiness_state"] == "MEDIA_CONTRACT_VALID"


def test_readiness_valid_state_does_not_create_error():
    result = ProductionResult(package_dir="out")
    readiness = artifact_readiness.ReadinessReport(
        "UPLOAD_PACKAGE_VALID",
        {
            "MEDIA_VALID": True,
            "MEDIA_CONTRACT_VALID": True,
            "UPLOAD_PACKAGE_VALID": True,
            "PUBLISH_READY": False,
        },
        [],
        [],
    )

    v3_pipeline._apply_readiness_contract(result, readiness)

    assert result.errors == []
    assert result.artifacts["v3_readiness_state"] == "UPLOAD_PACKAGE_VALID"


def test_resolve_final_video_rejects_invalid_v3_artifact(monkeypatch, tmp_path):
    package = Path(tmp_path)
    (package / "v3_blueprint.json").write_text("{}", encoding="utf-8")
    (package / "final.v3.mp4").write_bytes(b"invalid")
    (package / "final.mp4").write_bytes(b"also-invalid")

    def reject(_path):
        raise RuntimeError("bad media")

    monkeypatch.setattr(artifact_readiness, "probe_media", reject)
    assert artifact_readiness.resolve_final_video(package) is None


def test_v3_audience_profile_and_research_summary_cover_editorial_metadata(monkeypatch):
    monkeypatch.setenv("AIVF_DISABLE_SEMANTIC", "1")
    blueprint = create_v3_blueprint("Minecraft clutch", config=V3Config(target_seconds=12))
    payload = blueprint.to_dict()
    payload["audience"] = "gaming viewers"
    payload["source_metadata"] = {"rights_status": "owned"}

    summary = v3_pipeline._research_summary_from_blueprint(
        payload,
        {"scene_count": 2, "top_scenes": [{"id": "scene_1"}]},
    )

    assert summary["audience_profile"]["tone"] == "energetic"
    assert summary["target_total_seconds"] == blueprint.duration
    assert summary["cuts_per_minute"] > 0
    assert summary["source_metadata"]["rights_status"] == "owned"
    assert summary["v3_directives"]["disable_templates"] is True
    assert summary["v3_directives"]["blueprint_contract"] == "3.0.0"


def test_v3_footage_evidence_ranks_and_serializes_scenes(monkeypatch):
    scenes = [
        Scene(
            "low",
            2.0,
            4.0,
            description="low",
            transcript="later",
            objects=["tree"],
            text=["TEXT"],
            motion_score=0.2,
            audio_energy=0.3,
            face_count=1,
            importance_score=0.2,
        ),
        Scene(
            "high",
            0.0,
            2.0,
            description="high",
            transcript="hook",
            objects=["player"],
            text=["HOOK"],
            motion_score=0.9,
            audio_energy=0.8,
            face_count=2,
            importance_score=0.9,
        ),
    ]
    monkeypatch.setattr(v3_pipeline, "analyze_video", lambda *args, **kwargs: scenes)

    evidence = v3_pipeline._build_footage_evidence("source.mp4", enable_ocr=True, min_scenes=2)

    assert evidence["scene_count"] == 2
    assert evidence["top_scenes"][0]["id"] == "high"
    assert evidence["top_scenes"][0]["motion_score"] == 0.9
    assert evidence["top_scenes"][0]["audio_energy"] == 0.8
    assert evidence["top_scenes"][0]["objects"] == ["player"]
    assert evidence["top_scenes"][0]["text"] == ["HOOK"]


def test_v3_timeline_contract_accepts_valid_boundaries_and_rejects_corruption(tmp_path):
    package = Path(tmp_path)
    blueprint_payload = {
        "clip_plan": [
            {"start": 0.0, "end": 2.0},
            {"start": 2.0, "end": 5.0},
        ],
    }
    (package / "timeline.json").write_text(
        json.dumps({
            "duration": 5.0,
            "segments": [
                {"start": 0.0, "end": 2.0},
                {"start": 2.0, "end": 5.0},
            ],
        }),
        encoding="utf-8",
    )

    assert v3_pipeline._validate_timeline_contract(package, blueprint_payload, 5.0) is None

    (package / "timeline.json").write_text(
        json.dumps({
            "duration": 5.0,
            "segments": [{"start": 0.0, "end": 3.0}, {"start": 2.0, "end": 5.0}],
        }),
        encoding="utf-8",
    )
    with pytest.raises(v3_pipeline.RenderContractError, match="diverges from blueprint"):
        v3_pipeline._validate_timeline_contract(package, blueprint_payload, 5.0)

    (package / "timeline.json").write_text(
        json.dumps({
            "duration": 4.0,
            "segments": [
                {"start": 0.0, "end": 2.0},
                {"start": 2.0, "end": 5.0},
            ],
        }),
        encoding="utf-8",
    )
    with pytest.raises(v3_pipeline.RenderContractError, match="timeline duration diverges"):
        v3_pipeline._validate_timeline_contract(package, blueprint_payload, 5.0)


def test_v3_cleanup_removes_only_known_transients(tmp_path):
    package = Path(tmp_path)
    for name in (
        "final.v3.retention.mp4",
        ".final.v3.normalized.mp4",
        ".final.v3.audio-normalized.mp4",
        ".aivf-render.partial",
    ):
        (package / name).write_bytes(b"x")
    thumb = package / "aivf-v3-thumb-example"
    thumb.mkdir()
    (thumb / "frame.jpg").write_bytes(b"x")
    keep = package / "final.v3.mp4"
    keep.write_bytes(b"canonical")

    v3_pipeline._cleanup_v3_transients(package)

    assert not (package / "final.v3.retention.mp4").exists()
    assert not (package / ".final.v3.normalized.mp4").exists()
    assert not (package / ".final.v3.audio-normalized.mp4").exists()
    assert not (package / ".aivf-render.partial").exists()
    assert not thumb.exists()
    assert keep.read_bytes() == b"canonical"


def test_v3_prepare_blueprint_persists_source_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_DISABLE_SEMANTIC", "1")
    source_metadata = {"rights_status": "owned", "declared_by": "tester"}

    blueprint, payload, blueprint_path = v3_pipeline._prepare_blueprint(
        "A subject",
        "context",
        8.0,
        "youtube_shorts",
        "general short-form viewers",
        120,
        Path(tmp_path),
        None,
        source_metadata,
    )

    stored = json.loads(blueprint_path.read_text(encoding="utf-8"))
    assert blueprint.duration == 8.0
    assert payload["source_metadata"] == source_metadata
    assert stored["source_metadata"] == source_metadata


def test_v3_baseline_request_is_contract_derived(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_DISABLE_SEMANTIC", "1")
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    payload = blueprint.to_dict()
    request = v3_pipeline._build_baseline_request(
        input_video="source.mp4",
        topic="A subject",
        package_dir=str(tmp_path),
        target_seconds=8.0,
        platform="youtube_shorts",
        blueprint=blueprint,
        blueprint_payload=payload,
        footage_evidence={"scene_count": 1, "top_scenes": []},
        model_key=None,
        skip_qc=False,
        music_path=None,
        enable_ocr=False,
        enable_object_detection=True,
        enable_diarization=False,
        diarization_token=None,
    )

    assert request.input_video == "source.mp4"
    assert request.platform == "youtube_shorts"
    assert request.target_seconds == 8.0
    assert request.render_plan.retention_map == ()
    assert request.research_summary["v3_directives"]["blueprint_contract"] == "3.0.0"


def test_v3_audio_normalization_returns_original_when_disabled(monkeypatch, tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"video")
    monkeypatch.setenv("AIVF_EBU_R128", "0")
    assert v3_pipeline._normalize_final_audio(tmp_path, str(source)) == str(source)


def test_v3_audio_normalization_replaces_source_on_success(monkeypatch, tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    normalized = tmp_path / ".final.v3.audio-normalized.mp4"

    monkeypatch.setenv("AIVF_EBU_R128", "1")
    monkeypatch.setattr(
        v3_pipeline,
        "analyze_media",
        lambda *args, **kwargs: {"summary": {"has_audio": True}},
    )

    def fake_normalize(_source, destination):
        Path(destination).write_bytes(b"normalized")

    monkeypatch.setattr(v3_pipeline, "normalize_loudness", fake_normalize)

    assert v3_pipeline._normalize_final_audio(tmp_path, str(source)) == str(source)
    assert source.read_bytes() == b"normalized"
    assert not normalized.exists()


def test_stage_runner_overwrites_stale_readiness_after_failure(tmp_path):
    from ai_video_factory.v3_exceptions import V3PipelineError

    class SeedThenFailStage:
        name = "seed_then_fail"

        def run(self, context):
            context.package.mkdir(parents=True, exist_ok=True)
            (context.package / "v3_readiness.json").write_text(
                json.dumps({"state": "UPLOAD_PACKAGE_VALID", "checks": {"MEDIA_VALID": True}}),
                encoding="utf-8",
            )
            raise V3PipelineError("intentional stage failure")

    request = v3_stage_pipeline.V3Request(
        input_video="missing.mp4",
        topic="failure test",
        package_dir=str(tmp_path),
        target_seconds=8.0,
        platform="youtube_shorts",
        audience="general short-form viewers",
        bpm=120,
    )
    result = v3_stage_pipeline.V3PipelineRunner(
        request,
        stages=(SeedThenFailStage(),),
    ).run()

    readiness = json.loads((tmp_path / "v3_readiness.json").read_text(encoding="utf-8"))
    assert result.errors
    assert readiness["state"] == "FAILED"
    assert readiness["checks"]["MEDIA_VALID"] is False
    assert (tmp_path / "v3_failure.json").is_file()
    assert (tmp_path / "v3_stage_timings.json").is_file()


def test_semantic_qc_catches_a_sample_length_dead_scene(monkeypatch, tmp_path):
    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    scenes = [
        Scene("active-1", 0.0, 2.5, description="active", motion_score=0.8, audio_energy=0.7),
        Scene("dead", 2.5, 5.0, description="quiet", motion_score=0.0, audio_energy=0.0),
        Scene("active-2", 5.0, 7.5, description="active", motion_score=0.8, audio_energy=0.7),
    ]
    monkeypatch.setattr(v3_semantic_qc, "probe_media", lambda _path: {"duration": 30.0})
    monkeypatch.setattr(v3_semantic_qc, "analyze_video", lambda *args, **kwargs: scenes)

    report = v3_semantic_qc.analyze_render_semantics(str(video))

    # The scene must be detected at exactly the sampled 2.5s length, while
    # an isolated gap remains a warning rather than a release blocker.
    assert report["ok"] is True
    assert report["dead_moments"] == [{"start": 2.5, "end": 5.0, "duration": 2.5}]
    assert "isolated low-motion/low-audio section detected" in report["warnings"]
    assert report["dead_gap_blocking"] is False


def test_v3_package_reset_protects_symlinked_external_source(tmp_path):
    package = Path(tmp_path)
    external = package.parent / "external-source.mp4"
    external.write_bytes(b"source-media")
    source = package / "final.mp4"
    source.symlink_to(external)
    (package / "final.v3.mp4").write_bytes(b"stale-output")
    (package / "v3_readiness.json").write_text(
        json.dumps({"state": "UPLOAD_PACKAGE_VALID"}),
        encoding="utf-8",
    )

    with pytest.raises(v3_stage_pipeline.V3InputError, match="generated package filename"):
        v3_stage_pipeline._reset_v3_package(package, input_video=str(source))

    assert external.read_bytes() == b"source-media"
    assert source.is_symlink()
    assert not (package / "final.v3.mp4").exists()
    readiness = json.loads((package / "v3_readiness.json").read_text(encoding="utf-8"))
    assert readiness["state"] == "FAILED"


def test_v3_preflight_failure_replaces_stale_readiness(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-media")
    (tmp_path / "v3_readiness.json").write_text(
        json.dumps(
            {
                "state": "UPLOAD_PACKAGE_VALID",
                "checks": {
                    "MEDIA_VALID": True,
                    "MEDIA_CONTRACT_VALID": True,
                    "UPLOAD_PACKAGE_VALID": True,
                    "PUBLISH_READY": True,
                },
            }
        ),
        encoding="utf-8",
    )

    import ai_video_factory.v3_capabilities as v3_capabilities

    monkeypatch.setattr(
        v3_capabilities,
        "validate_capabilities",
        lambda: (_ for _ in ()).throw(ValueError("capability registry invalid")),
    )

    request = v3_stage_pipeline.V3Request(
        input_video=str(source),
        topic="preflight failure",
        package_dir=str(tmp_path),
        target_seconds=8.0,
        platform="youtube_shorts",
        audience="general short-form viewers",
        bpm=120,
    )
    result = v3_stage_pipeline.V3PipelineRunner(request).run()

    readiness = json.loads(
        (tmp_path / "v3_readiness.json").read_text(encoding="utf-8")
    )
    assert result.errors
    assert readiness["state"] == "FAILED"
    assert readiness["checks"]["PUBLISH_READY"] is False
    assert (tmp_path / "v3_failure.json").is_file()
