from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_video_factory.media_health import probe_media
from ai_video_factory.v3_pipeline import run_v3_pipeline


@pytest.mark.integration
def test_v3_golden_media_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is required for the golden media test")

    monkeypatch.setenv("AIVF_ENV", "test")
    monkeypatch.setenv("AIVF_ALLOW_SKIP_QC", "1")
    monkeypatch.setenv("AIVF_V3_SEMANTIC_QC", "0")
    monkeypatch.setenv("AIVF_DEEP_FINAL_MEDIA_QC", "0")
    monkeypatch.setenv("AIVF_EBU_R128", "0")
    monkeypatch.setenv("AIVF_REQUIRE_HUMAN_REVIEW", "0")
    monkeypatch.setenv("AIVF_MIN_FREE_DISK_MB", "64")

    source = tmp_path / "golden-source.mp4"
    package = tmp_path / "golden-package"

    subprocess.run(
        [
            ffmpeg,
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "10", "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            str(source),
        ],
        check=True,
    )

    result = run_v3_pipeline(
        str(source),
        "Minecraft clutch survival",
        str(package),
        target_seconds=8.0,
        platform="youtube_shorts",
        audience="gaming viewers",
        bpm=120,
        enable_object_detection=False,
    )

    assert result.errors == []
    assert result.ok is True

    final_video = package / "final.v3.mp4"
    assert final_video.is_file()
    media = probe_media(str(final_video))
    assert media["width"] == 1080
    assert media["height"] == 1920
    assert media["duration"] == pytest.approx(8.0, abs=0.08)
    assert media["has_audio"] is True

    required = (
        "v3_blueprint.json",
        "timeline.json",
        "v3_render_qc.json",
        "v3_semantic_qc.json",
        "v3_readiness.json",
        "upload_package.json",
        "metadata_guardrails.json",
        "provenance.json",
        "diagnostics.json",
        "environment_fingerprint.json",
        "artifact_manifest.json",
        "release_evidence.json",
        "v3_scores.json",
        "v3_stage_timings.json",
    )
    assert all((package / name).is_file() for name in required)

    blueprint = json.loads((package / "v3_blueprint.json").read_text(encoding="utf-8"))
    scores = json.loads((package / "v3_scores.json").read_text(encoding="utf-8"))
    readiness = json.loads((package / "v3_readiness.json").read_text(encoding="utf-8"))
    timings = json.loads((package / "v3_stage_timings.json").read_text(encoding="utf-8"))
    assert blueprint["version"] == "3.0.0"
    assert {"technical_validity", "creative_quality", "performance_heuristic"} <= set(scores)
    assert readiness["checks"]["MEDIA_VALID"] is True
    assert readiness["checks"]["MEDIA_CONTRACT_VALID"] is True
    assert timings["stages_ms"]
    assert timings["total_ms"] > 0
