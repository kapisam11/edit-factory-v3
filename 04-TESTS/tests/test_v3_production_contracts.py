from pathlib import Path

from ai_video_factory import artifact_readiness, render_engine, v3_pipeline
from ai_video_factory.production_models import ProductionResult


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
