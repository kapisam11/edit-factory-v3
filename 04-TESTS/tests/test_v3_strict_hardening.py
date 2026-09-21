from __future__ import annotations

import subprocess

import pytest

from ai_video_factory.edit_planner import build_timeline, choose_scene
from ai_video_factory.production_models import Scene
from ai_video_factory.production_pipeline import _platform_aspect_ratio
from ai_video_factory.subtitle_renderer import build_drawtext_filter, estimate_word_timing
from ai_video_factory.v3_engine import EditType, V3Config, create_v3_blueprint
from ai_video_factory.v3_quality import RenderContractError, enforce_retention_events, normalize_duration, strict_render_check
from ai_video_factory.v3_semantics import combined_scores, lexical_scores, semantic_similarity


def test_all_supported_edit_types_produce_distinct_editorial_strategies():
    blueprints = {item: create_v3_blueprint("A subject", edit_type=item.value) for item in EditType}
    signatures = {item: (tuple(beat.purpose for beat in blueprint.clip_plan[:6]), tuple(beat.camera_motion for beat in blueprint.clip_plan[:4]), tuple(beat.transition for beat in blueprint.clip_plan[:5])) for item, blueprint in blueprints.items()}
    assert len(set(signatures.values())) >= 8


def test_strict_duration_and_platform_contracts():
    with pytest.raises(ValueError, match="youtube_shorts"):
        V3Config(target_seconds=61, platform="youtube_shorts").validate()
    with pytest.raises(ValueError, match="unsupported platform"):
        V3Config(platform="unknown").validate()
    with pytest.raises(ValueError, match="between 8 and 180"):
        V3Config(target_seconds=float("inf")).validate()
    assert V3Config(target_seconds=30, platform="youtube_shorts").validate() is None


def test_platform_dimensions_drive_ratio_without_hardcoded_vertical_assumption():
    assert _platform_aspect_ratio({"width": 1080, "height": 1920}) == "9:16"
    assert _platform_aspect_ratio({"width": 1920, "height": 1080}) == "16:9"
    assert _platform_aspect_ratio({"width": 1080, "height": 1080}) == "1:1"
    assert _platform_aspect_ratio({"width": 0, "height": 1080}) == "9:16"


def test_semantic_module_has_deterministic_fallback():
    lexical = lexical_scores("a loyal friend protected everyone")
    combined = combined_scores("a loyal friend protected everyone")
    assert lexical["trust"] > 0
    assert set(combined) == set(lexical)
    assert all(value == value for value in combined.values())


def test_semantic_similarity_score_is_not_centered_at_half(monkeypatch):
    import ai_video_factory.v3_semantics as semantics
    monkeypatch.setattr(semantics, "_semantic_vector_scores", lambda *args, **kwargs: [0.0])
    monkeypatch.delenv("AIVF_DISABLE_SEMANTIC", raising=False)
    assert semantic_similarity("completely unrelated query", "different content") == 0.0


def test_scene_match_fails_closed_when_semantic_relevance_is_too_low(monkeypatch):
    import ai_video_factory.v3_semantics as semantics
    monkeypatch.setattr(semantics, "_semantic_vector_scores", lambda *args, **kwargs: [0.0])
    monkeypatch.delenv("AIVF_DISABLE_SEMANTIC", raising=False)
    scenes = [Scene("a", 0, 2, description="green grass field", importance_score=0.99), Scene("b", 2, 4, description="blue ocean waves", importance_score=0.01)]
    with pytest.raises(ValueError, match="minimum relevance confidence"):
        choose_scene("ancient volcano eruption", scenes, (), 2.0, "hook", min_match_score=0.15)


def test_scene_match_uses_visual_fallback_when_only_generic_time_ranges_exist(monkeypatch):
    import ai_video_factory.v3_semantics as semantics
    monkeypatch.setattr(semantics, "_semantic_vector_scores", lambda *args, **kwargs: [0.0])
    monkeypatch.delenv("AIVF_DISABLE_SEMANTIC", raising=False)
    scenes = [
        Scene("a", 0, 2, description="Source footage from 0.0s to 2.0s", importance_score=0.99, motion_score=0.8),
        Scene("b", 2, 4, description="Source footage from 2.0s to 4.0s", importance_score=0.01, motion_score=0.1),
    ]
    scene, score = choose_scene("ancient volcano eruption", scenes, (), 2.0, "hook", min_match_score=0.15)
    assert scene.id == "a"
    assert score > 0


def test_scene_index_can_provision_minimum_v3_regions(monkeypatch):
    import ai_video_factory.scene_intelligence as scene_intelligence

    monkeypatch.setattr(scene_intelligence, "_ffprobe_duration", lambda _path: 12.0)
    monkeypatch.setattr(scene_intelligence, "_sample_frames", lambda *args, **kwargs: iter(()))
    scenes = scene_intelligence.analyze_video("fixture.mp4", min_scenes=6)
    assert len(scenes) == 6
    assert scenes[-1].end == 12.0


def test_scene_match_ignores_sparse_noisy_ocr_for_rejection(monkeypatch):
    import ai_video_factory.v3_semantics as semantics
    monkeypatch.setattr(semantics, "_semantic_vector_scores", lambda *args, **kwargs: [0.0])
    monkeypatch.delenv("AIVF_DISABLE_SEMANTIC", raising=False)
    scenes = [
        Scene("a", 0, 2, description="Source footage from 0.0s to 2.0s", text=["SGALATION PAYO"], importance_score=0.99, motion_score=0.8),
        Scene("b", 2, 4, description="Source footage from 2.0s to 4.0s", text=["NOISE"], importance_score=0.01, motion_score=0.1),
    ]
    scene, score = choose_scene("ancient volcano eruption", scenes, (), 2.0, "hook", min_match_score=0.15)
    assert scene.id == "a"
    assert score > 0


def test_v3_timeline_uses_exact_blueprint_boundaries(monkeypatch):
    monkeypatch.setenv("AIVF_DISABLE_SEMANTIC", "1")
    scenes = [Scene(str(i), i * 2, (i + 1) * 2, description="subject action scene", importance_score=0.9, motion_score=0.8) for i in range(6)]
    directives = {"clip_plan": [{"start": 0.0, "end": 3.0, "purpose": "Hook", "visual_style": "subject action"}, {"start": 3.0, "end": 7.0, "purpose": "Context", "visual_style": "action scene"}, {"start": 7.0, "end": 12.0, "purpose": "Payoff", "visual_style": "subject action"}], "platform_profile": {"max_seconds": 60}, "min_scene_match_score": 0.05, "retention_map": [{"time": 0.0, "kind": "zoom"}, {"time": 3.0, "kind": "motion"}]}
    timeline = build_timeline("subject starts moving then action reaches the payoff", scenes, total_seconds=12.0, creative_directives=directives)
    assert [(round(s.start, 3), round(s.end, 3)) for s in timeline.segments] == [(0.0, 3.0), (3.0, 7.0), (7.0, 12.0)]
    assert timeline.duration == 12.0


def test_subtitle_filter_escapes_ffmpeg_filter_metacharacters():
    timings = [{"word": "O'Reilly:%\\test", "start": 0.0, "end": 1.0}]
    filter_text = build_drawtext_filter(timings)
    assert "O\\'Reilly" in filter_text
    assert "\\:" in filter_text
    assert "\\%" in filter_text
    assert "\\\\test" in filter_text


def test_subtitle_timing_rejects_non_positive_duration():
    with pytest.raises(ValueError):
        estimate_word_timing("hello world", 0)
    with pytest.raises(ValueError):
        estimate_word_timing("hello world", float("nan"))


def test_media_qc_rejects_non_monotonic_retention_events(tmp_path):
    source = tmp_path / "not-media.mp4"
    source.write_bytes(b"not media")
    with pytest.raises(RenderContractError):
        enforce_retention_events(str(source), str(tmp_path / "out.mp4"), [{"time": 0.0}, {"time": 0.0}])


@pytest.mark.integration
def test_render_contract_normalizes_enforces_and_validates_duration(tmp_path):
    source = tmp_path / "source.mp4"; retained = tmp_path / "retained.mp4"; final = tmp_path / "final.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30", "-t", "3", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
    enforce_retention_events(str(source), str(retained), [{"time": 0.0, "kind": "zoom"}, {"time": 2.0, "kind": "motion"}])
    normalize_duration(str(retained), str(final), 5.0)
    report = strict_render_check(str(final), target_seconds=5.0, platform_profile={"width": 1080, "height": 1920, "max_seconds": 60}, retention_events=[{"time": 0.0}, {"time": 2.0}])
    assert report["ok"] is True
    assert abs(report["media"]["duration"] - 5.0) <= 0.08
    assert report["media"]["width"] == 1080
    assert report["media"]["height"] == 1920


def test_v3_planning_accepts_eight_second_target(monkeypatch):
    monkeypatch.setenv("AIVF_DISABLE_SEMANTIC", "1")
    from ai_video_factory.plan import make_idea
    summary = {
        "topic": "short V3 test",
        "target_total_seconds": 8.0,
        "strongest_angle": "The key moment",
        "main_conflict": "The moment changes everything",
        "why_care": "The payoff arrives quickly",
        "v3_directives": {"blueprint_contract": "3.0.0"},
    }
    idea = make_idea(summary)
    assert idea["structure"]["total_seconds"] == 8.0


def test_v3_input_validation_rejects_invalid_contract_values(tmp_path):
    from ai_video_factory.v3_pipeline import _validate_v3_inputs

    missing = tmp_path / "missing.mp4"
    with pytest.raises(RenderContractError, match="missing or not a file"):
        _validate_v3_inputs(str(missing), "topic", 30, "youtube_shorts", "audience", 120)

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(RenderContractError, match="empty"):
        _validate_v3_inputs(str(empty), "topic", 30, "youtube_shorts", "audience", 120)

    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    cases = [
        (lambda: _validate_v3_inputs(str(source), "", 30, "youtube_shorts", "audience", 120), ValueError),
        (lambda: _validate_v3_inputs(str(source), "topic", "bad", "youtube_shorts", "audience", 120), ValueError),
        (lambda: _validate_v3_inputs(str(source), "topic", 7, "youtube_shorts", "audience", 120), ValueError),
        (lambda: _validate_v3_inputs(str(source), "topic", 30, "", "audience", 120), ValueError),
        (lambda: _validate_v3_inputs(str(source), "topic", 30, "youtube_shorts", "audience" * 100, 120), ValueError),
        (lambda: _validate_v3_inputs(str(source), "topic", 30, "youtube_shorts", "audience", "bad"), ValueError),
        (lambda: _validate_v3_inputs(str(source), "topic", 30, "youtube_shorts", "audience", 300), ValueError),
    ]
    for invoke, error in cases:
        with pytest.raises(error):
            invoke()
