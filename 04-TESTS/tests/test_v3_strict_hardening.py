from __future__ import annotations

import os
import subprocess

import pytest

from ai_video_factory.v3_engine import EditType, V3Config, create_v3_blueprint
from ai_video_factory.v3_quality import normalize_duration, strict_render_check
from ai_video_factory.v3_semantics import combined_scores, lexical_scores


def test_all_supported_edit_types_produce_distinct_editorial_strategies():
    blueprints = {item: create_v3_blueprint("A subject", edit_type=item.value) for item in EditType}
    signatures = {
        item: (
            tuple(beat.purpose for beat in blueprint.clip_plan[:6]),
            tuple(beat.camera_motion for beat in blueprint.clip_plan[:4]),
            tuple(beat.transition for beat in blueprint.clip_plan[:5]),
        )
        for item, blueprint in blueprints.items()
    }
    assert len(set(signatures.values())) >= 8


def test_strict_duration_and_platform_contracts():
    with pytest.raises(ValueError, match="youtube_shorts"):
        V3Config(target_seconds=61, platform="youtube_shorts").validate()
    with pytest.raises(ValueError, match="unsupported platform"):
        V3Config(platform="unknown").validate()
    with pytest.raises(ValueError, match="between 8 and 180"):
        V3Config(target_seconds=float("inf")).validate()
    assert V3Config(target_seconds=30, platform="youtube_shorts").validate() is None


def test_semantic_module_has_deterministic_fallback():
    lexical = lexical_scores("a loyal friend protected everyone")
    combined = combined_scores("a loyal friend protected everyone")
    assert lexical["trust"] > 0
    assert set(combined) == set(lexical)
    assert all(value == value for value in combined.values())


@pytest.mark.integration
def test_render_contract_normalizes_and_validates_duration(tmp_path):
    source = tmp_path / "source.mp4"
    final = tmp_path / "final.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30",
            "-t", "3", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "3", "-shortest", "-c:v", "libx264", "-c:a", "aac",
            str(source),
        ],
        check=True,
    )
    normalize_duration(str(source), str(final), 5.0)
    report = strict_render_check(
        str(final),
        target_seconds=5.0,
        platform_profile={"width": 1080, "height": 1920, "max_seconds": 60},
        retention_events=[{"time": 0.0}, {"time": 2.0}, {"time": 4.0}],
    )
    assert report["ok"] is True
    assert abs(report["media"]["duration"] - 5.0) <= 0.08
    assert report["media"]["width"] == 1080
    assert report["media"]["height"] == 1920
