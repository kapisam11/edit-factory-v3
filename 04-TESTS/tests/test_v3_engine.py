from __future__ import annotations

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
                "three escalation Rising tension; four peak decisive Climax; "
                "five aftermath Payoff; six final impact Resolution."
            ),
            transcript=(
                f"Line {i}: result-before-context Hook setup obstacle Context escalation "
                "Rising tension peak decisive Climax aftermath Payoff final impact Resolution."
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
    assert "punch-in" in timeline.segments[0].label or "micro-zoom" in timeline.segments[0].label
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
