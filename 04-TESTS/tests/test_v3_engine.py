from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ai_video_factory.edit_planner import build_timeline
from ai_video_factory.production_models import Scene
from ai_video_factory.v3_engine import (
    EditType,
    V3Config,
    create_v3_blueprint,
    validate_blueprint,
)
from ai_video_factory.v3_pipeline import _research_summary_from_blueprint
from ai_video_factory.v3_quality import RenderContractError


def test_v3_blueprint_enforces_original_editor_contract():
    blueprint = create_v3_blueprint(
        "Wemmbu",
        context="He risked everything for his friends in a final dangerous fight.",
        config=V3Config(target_seconds=30.0, bpm=120, retention_interval=2.0),
    )

    validate_blueprint(blueprint)
    assert blueprint.version == "3.0.0"
    assert len(blueprint.capabilities) == 40
    assert len(blueprint.hooks) >= 3
    assert blueprint.hooks[0].score >= blueprint.hooks[-1].score
    assert blueprint.clip_plan[0].purpose == "Hook"
    assert blueprint.clip_plan[-1].purpose in {"Final impact", "Payoff"}
    assert any(clip.purpose in {"Payoff", "Climax"} for clip in blueprint.clip_plan)
    assert abs(blueprint.clip_plan[-1].end - 30.0) < 0.02
    assert all(2 <= len(clip.text_overlay.split()) <= 6 for clip in blueprint.clip_plan)
    assert all((b.time - a.time) <= 3.0 for a, b in zip(blueprint.retention_map, blueprint.retention_map[1:]))
    assert blueprint.quality.passed is True
    assert blueprint.metrics["retention_score"] >= 80


def test_each_edit_type_has_a_real_strategy():
    funny = create_v3_blueprint("A chaotic moment", edit_type="Funny")
    documentary = create_v3_blueprint("A historical event", edit_type="Documentary")
    tribute = create_v3_blueprint("A loyal friend", edit_type="Tribute")

    assert [b.purpose for b in funny.clip_plan[:6]] != [b.purpose for b in documentary.clip_plan[:6]]
    assert funny.clip_plan[0].camera_motion != documentary.clip_plan[0].camera_motion
    assert tribute.clip_plan[1].transition == "match cut"
    assert any(b.purpose == "Punchline" for b in funny.clip_plan)
    assert any(b.purpose == "Evidence" for b in documentary.clip_plan)


def test_platform_limits_are_enforced():
    with pytest.raises(ValueError, match="youtube_shorts"):
        create_v3_blueprint("Topic", config=V3Config(target_seconds=61, platform="youtube_shorts"))
    with pytest.raises(ValueError, match="unsupported platform"):
        create_v3_blueprint("Topic", config=V3Config(platform="somewhere_else"))


def test_v3_directives_reach_real_timeline():
    scenes = [
        Scene(
            id=f"scene_{i}",
            start=float(i * 3),
            end=float((i + 1) * 3),
            description=(
                f"Scene {i}: One result-before-context Hook; two setup/obstacle Context; "
                "three escalation Rising tension; four momentum action Escalation peak decisive Climax; "
                "five aftermath Payoff; six final impact Resolution; effort detail curiosity."
            ),
            transcript=(
                f"Line {i}: result-before-context Hook setup obstacle Context escalation "
                "Rising tension momentum action Escalation peak decisive Climax aftermath Payoff final impact Resolution; "
                "effort detail curiosity."
            ),
            motion_score=0.6,
            importance_score=0.7,
        )
        for i in range(6)
    ]
    blueprint = create_v3_blueprint(
        "A comeback", edit_type="Motivational", config=V3Config(target_seconds=12)
    )
    plan = blueprint.to_dict()
    timeline = build_timeline(
        "One\ntwo\nthree\nfour\nfive\nsix",
        scenes,
        total_seconds=12,
        creative_directives={"clip_plan": plan["clip_plan"], "edit_type": plan["edit_type"]},
    )

    assert len(timeline.segments) == 6
    assert timeline.segments[0].role == "hook"
    assert timeline.segments[3].role == "climax"
    # V3 chooses motion from footage characteristics. The label must expose the
    # chosen renderable motion rather than assuming every hook is a punch-in.
    allowed_motion_labels = {"punch-in", "micro-zoom", "reframe", "tracking", "subtle-parallax"}
    assert any(token in timeline.segments[0].label for token in allowed_motion_labels)
    assert timeline.segments[0].effects
    assert abs(timeline.duration - 12.0) < 0.02


def test_blueprint_is_json_serializable_and_has_platform_profiles():
    blueprint = create_v3_blueprint("MrBeast", context="He turned generosity into entertainment.")
    payload = blueprint.to_dict()
    assert payload["version"] == "3.0.0"
    assert len(payload["capabilities"]) == 40
    assert payload["platform_variants"]["youtube_shorts"]["width"] == 1080
    assert payload["platform_variants"]["youtube_shorts"]["height"] == 1920


def test_v3_research_summary_feeds_existing_renderer_contract():
    blueprint = create_v3_blueprint("Eggchan", context="A loyal friend who stayed when others left.")
    payload = blueprint.to_dict()
    payload["platform"] = "youtube_shorts"
    summary = _research_summary_from_blueprint(payload)

    assert summary["emotion"] == blueprint.core_idea.target_emotion
    assert summary["strongest_angle"] == blueprint.core_idea.emotional_angle
    assert summary["v3_edit_type"] == blueprint.edit_type
    assert summary["v3_quality_score"] == blueprint.quality.score
    assert summary["cuts_per_minute"] > 0
    assert summary["v3_directives"]["edit_type"] == blueprint.edit_type
    assert len(summary["v3_directives"]["retention_map"]) == len(blueprint.retention_map)


def test_requested_edit_type_is_exactly_one_supported_type():
    for edit_type in EditType:
        blueprint = create_v3_blueprint("A subject", edit_type=edit_type.value)
        assert blueprint.edit_type == edit_type.value



def test_v3_blueprint_round_trip_is_strict_and_immutable():
    blueprint = create_v3_blueprint("Wemmbu", context="A difficult choice", config=V3Config(target_seconds=30))
    payload = blueprint.to_dict()
    restored = blueprint.from_dict(payload)

    assert restored.schema_version == "3.0.1"
    assert restored.platform == "youtube_shorts"
    assert restored.duration == blueprint.duration
    assert isinstance(restored.hooks, tuple)
    assert isinstance(restored.clip_plan, tuple)

    with pytest.raises(FrozenInstanceError):
        restored.clip_plan += (restored.clip_plan[0],)
    with pytest.raises(TypeError):
        restored.platform_variants["youtube_shorts"] = {}

    payload["clip_plan"][0]["start"] = -1
    with pytest.raises(ValueError, match="monotonic|timing"):
        blueprint.from_dict(payload)


def test_v3_blueprint_deserialization_rejects_missing_and_wrong_schema():
    blueprint = create_v3_blueprint("A subject")
    payload = blueprint.to_dict()

    del payload["music"]
    with pytest.raises(ValueError, match="missing required fields"):
        blueprint.from_dict(payload)

    payload = blueprint.to_dict()
    payload["schema_version"] = "2.0.0"
    with pytest.raises(ValueError, match="schema version"):
        blueprint.from_dict(payload)


def test_v3_blueprint_exposes_contract_platform_and_duration():
    blueprint = create_v3_blueprint(
        "A subject",
        config=V3Config(target_seconds=12, platform="tiktok"),
    )
    assert blueprint.platform == "tiktok"
    assert blueprint.duration == 12.0
    assert blueprint.schema_version == blueprint.version == "3.0.0"



def test_v3_metrics_use_explicit_heuristic_metadata_and_bounded_scores():
    blueprint = create_v3_blueprint("A subject")
    assert blueprint.metric_metadata.method == "heuristic"
    assert blueprint.metric_metadata.confidence == "low"
    assert all(0.0 <= float(value) <= 100.0 for value in blueprint.metrics.values())

def test_v3_finalization_does_not_promote_failed_qc_output(tmp_path, monkeypatch):
    import ai_video_factory.v3_pipeline as pipeline
    from types import SimpleNamespace

    package = tmp_path / "package"
    package.mkdir()
    rendered = package / "legacy-render.mp4"
    rendered.write_bytes(b"rendered")
    result = SimpleNamespace(final_video=str(rendered))
    payload = {
        "clip_plan": [{"start": 0.0, "end": 1.0}],
        "retention_map": [],
        "platform": "youtube_shorts",
        "platform_variants": {"youtube_shorts": {"width": 1080, "height": 1920, "max_seconds": 60}},
        "packaging": {"final_video_name": "final.v3.mp4"},
    }
    (package / "timeline.json").write_text(
        '{"segments":[{"start":0.0,"end":1.0}],"duration":1.0}', encoding="utf-8"
    )
    monkeypatch.setattr(pipeline, "enforce_retention_events", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "normalize_duration", lambda _a, output, _d: Path(output).write_bytes(b"normalized"))
    monkeypatch.setattr(
        pipeline,
        "strict_render_check",
        lambda *_a, **_k: {"ok": False, "errors": ["bad codec"], "warnings": []},
    )
    with pytest.raises(RenderContractError, match="bad codec"):
        pipeline._finalize_v3_media(result, package, payload, 1.0)
    assert not (package / "final.v3.mp4").exists()
    assert not (package / ".final.v3.normalized.mp4").exists()

def test_v3_platform_deserialization_normalizes_case_variants():
    blueprint = create_v3_blueprint("A subject")
    payload = blueprint.to_dict()
    payload["platform"] = "TikTok"
    payload.pop("platform_profile", None)
    restored = blueprint.from_dict(payload)
    assert restored.platform == "tiktok"


def test_v3_quality_flags_are_strict_booleans():
    blueprint = create_v3_blueprint("A subject")
    payload = blueprint.to_dict()
    payload["quality"]["passed"] = "false"
    with pytest.raises((TypeError, ValueError), match="boolean|malformed typed data"):
        blueprint.from_dict(payload)


def test_v3_music_sync_points_must_be_monotonic():
    blueprint = create_v3_blueprint("A subject")
    payload = blueprint.to_dict()
    payload["music"]["sync_points"] = [0.0, 4.0, 3.0, blueprint.duration]
    with pytest.raises(ValueError, match="monotonic"):
        blueprint.from_dict(payload)


def test_retention_map_does_not_schedule_effects_from_time_alone(monkeypatch):
    monkeypatch.setenv("AIVF_DISABLE_SEMANTIC", "1")
    from ai_video_factory.v3_engine import (
        V3Config,
        analyze_core_idea,
        analyze_music,
        build_clip_plan,
        build_retention_map,
        choose_edit_type,
    )
    config = V3Config(target_seconds=30)
    core = analyze_core_idea("A subject")
    edit_type = choose_edit_type(core)
    clips = build_clip_plan(core, edit_type, config)
    music = analyze_music(core, config, clips)
    events = build_retention_map(config, clips, music)
    assert all(event.reason != "VISUAL_CONTINUITY" for event in events)
