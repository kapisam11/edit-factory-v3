from __future__ import annotations

import subprocess

import pytest

from ai_video_factory.edit_planner import build_timeline, choose_scene
from ai_video_factory.production_models import Scene
from ai_video_factory.v3_engine import EditType, V3Config, create_v3_blueprint
from ai_video_factory.v3_quality import enforce_retention_events, normalize_duration, strict_render_check
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


def test_scene_match_fails_closed_when_semantic_relevance_is_too_low(monkeypatch):
    monkeypatch.setenv("AIVF_DISABLE_SEMANTIC", "1")
    scenes = [
        Scene("a", 0, 2, description="green grass field", importance_score=0.99),
        Scene("b", 2, 4, description="blue ocean waves", importance_score=0.01),
    ]
    with pytest.raises(ValueError, match="minimum relevance confidence"):
        choose_scene("ancient volcano eruption", scenes, (), 2.0, "hook", min_match_score=0.15)


def test_v3_timeline_uses_exact_blueprint_boundaries(monkeypatch):
    monkeypatch.setenv("AIVF_DISABLE_SEMANTIC", "1")
    scenes = [
        Scene(str(i), i * 2, (i + 1) * 2, description="subject action scene", importance_score=0.9, motion_score=0.8)
        for i in range(6)
    ]
    directives = {
        "clip_plan": [
            {"start": 0.0, "end": 1.5, "purpose": "Hook", "visual_style": "subject"},
            {"start": 1.5, "end": 3.5, "purpose": "Context", "visual_style": "action"},
            {"start": 3.5, "end": 5.0, "purpose": "Payoff", "visual_style": "scene"},
        ],
        "platform_profile": {"max_seconds": 60},
        "min_scene_match_score": 0.05,
        "retention_map": [{"time": 0.0, "kind": "zoom"}, {"time": 3.0, "kind": "motion"}],
    }
    timeline = build_timeline("subject starts moving then action reaches the payoff", scenes, total_seconds=5.0, creative_directives=directives)
    assert [(round(s.start, 3), round(s.end, 3)) for s in timeline.segments] == [(0.0, 1.5), (1.5, 3.5), (3.5, 5.0)]
    assert timeline.duration == 5.0


@pytest.mark.integration
def test_render_contract_normalizes_enforces_and_validates_duration(tmp_path):
    source = tmp_path / "source.mp4"
    retained = tmp_path / "retained.mp4"
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
    enforce_retention_events(str(source), str(retained), [{"time": 0.0, "kind": "zoom"}, {"time": 2.0, "kind": "motion"}])
    normalize_duration(str(retained), str(final), 5.0)
    report = strict_render_check(
        str(final),
        target_seconds=5.0,
        platform_profile={"width": 1080, "height": 1920, "max_seconds": 60},
        retention_events=[{"time": 0.0}, {"time": 2.0}],
    )
    assert report["ok"] is True
    assert abs(report["media"]["duration"] - 5.0) <= 0.08
    assert report["media"]["width"] == 1080
    assert report["media"]["height"] == 1920
