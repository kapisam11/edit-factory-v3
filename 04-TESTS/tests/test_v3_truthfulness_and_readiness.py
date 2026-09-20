import json

import pytest

from ai_video_factory.artifact_readiness import evaluate_artifact
from ai_video_factory.v3_capabilities import CAPABILITIES, validate_capabilities


def test_capability_registry_exposes_truthful_evidence_levels():
    validate_capabilities()
    assert len(CAPABILITIES) == 40
    assert CAPABILITIES["human-editor reject pass"].display_name == "editorial reject-rule pass"
    assert CAPABILITIES["dead-moment detection"].status == "heuristic"
    assert CAPABILITIES["AI-slideshow guard"].display_name == "slideshow-cadence heuristic"
    assert "not equivalent to human review" in CAPABILITIES["payoff validation"].truth_note
    assert CAPABILITIES["emotion-first core idea"].status == "hybrid"


def test_artifact_readiness_stops_at_media_contract(monkeypatch, tmp_path):
    import ai_video_factory.artifact_readiness as readiness
    monkeypatch.setattr(
        readiness,
        "probe_media",
        lambda _path: {
            "duration": 8.0,
            "width": 1080,
            "height": 1920,
            "size": 123,
            "has_audio": True,
        },
    )
    report = evaluate_artifact(
        str(tmp_path / "video.mp4"),
        target_seconds=8.0,
        platform_profile={"width": 1080, "height": 1920},
        package_dir=str(tmp_path),
    )
    assert report.state == "MEDIA_CONTRACT_VALID"
    assert report.checks["MEDIA_VALID"] is True
    assert report.checks["MEDIA_CONTRACT_VALID"] is True
    assert report.checks["UPLOAD_PACKAGE_VALID"] is False
    assert report.checks["PUBLISH_READY"] is False

def test_semantic_qc_disable_is_development_only(monkeypatch, tmp_path):
    source = tmp_path / "input.mp4"
    source.write_bytes(b"x")
    monkeypatch.setenv("AIVF_V3_SEMANTIC_QC", "0")
    monkeypatch.delenv("AIVF_ENV", raising=False)
    from ai_video_factory.v3_pipeline import run_v3_pipeline
    with pytest.raises(ValueError, match="AIVF_V3_SEMANTIC_QC=0"):
        run_v3_pipeline(str(source), "source footage", str(tmp_path / "package"), target_seconds=8.0)

