import json
from pathlib import Path
from unittest.mock import patch

from ai_video_factory.edit_planner import build_timeline, timeline_to_composer_plan
from ai_video_factory.learning_recommender import recommend
from ai_video_factory.production_models import Scene
from ai_video_factory.production_pipeline import _generate_script, _normalize_model_script


def _scene(scene_id, start, end, description, importance=0.5, motion=0.5):
    return Scene(
        id=scene_id,
        start=start,
        end=end,
        description=description,
        importance_score=importance,
        motion_score=motion,
    )


def test_build_timeline_selects_relevant_scenes():
    scenes = [
        _scene("s1", 0, 4, "player opens secret chest", importance=0.9, motion=0.7),
        _scene("s2", 4, 8, "player walks through village", importance=0.2, motion=0.2),
        _scene("s3", 8, 12, "enemy attacks player with sword", importance=0.95, motion=0.9),
        _scene("s4", 12, 16, "player celebrates after victory", importance=0.7, motion=0.3),
    ]
    timeline = build_timeline(
        "Open the secret chest\nThen the enemy attacks\nHe finally wins",
        scenes,
        total_seconds=15,
    )

    assert len(timeline.segments) == 3
    assert not timeline.validate()
    assert timeline.segments[0].source_scene_id in {"s1", "s3"}
    assert timeline.duration > 0
    assert len(timeline_to_composer_plan(timeline)) == 3


def test_timeline_round_trip(tmp_path: Path):
    scenes = [_scene("s1", 0, 5, "a dramatic opening", importance=0.9, motion=0.8)]
    timeline = build_timeline("A dramatic opening", scenes, total_seconds=15)
    payload = timeline.to_dict()
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["segments"][0]["source_scene_id"] == "s1"
    assert loaded["aspect_ratio"] == "9:16"


def test_learning_recommender_uses_nearby_history():
    history = [
        {
            "platform": "youtube_shorts",
            "caption_style": "karaoke",
            "voice": "energetic",
            "cuts_per_minute": 19,
            "avg_shot_duration": 1.6,
            "hook_duration": 2.1,
            "music_energy": 0.82,
            "performance_score": 0.9,
        },
        {
            "platform": "youtube_shorts",
            "caption_style": "plain",
            "voice": "calm",
            "cuts_per_minute": 8,
            "avg_shot_duration": 3.4,
            "hook_duration": 4.0,
            "music_energy": 0.25,
            "performance_score": 0.2,
        },
    ]
    result = recommend(
        {"platform": "youtube_shorts", "cuts_per_minute": 18, "avg_shot_duration": 1.7},
        history,
        defaults={"caption_style": "plain"},
    )
    assert result.evidence_count == 2
    assert result.settings["caption_style"] == "karaoke"
    assert result.confidence > 0


def test_normalize_model_script_accepts_json_fences():
    response = "```json\n" + json.dumps({"lines": ["The hidden truth", "Everything changed."]}) + "\n```"
    assert _normalize_model_script(response) == "The hidden truth\nEverything changed."


def test_generate_script_uses_model_output_when_key_is_present():
    response = json.dumps({"lines": ["The hidden truth", "Everything changed.", "Nobody saw it coming."]})
    summary = {"topic": "Minecraft", "strongest_angle": "Betrayal", "emotion": "dramatic"}
    with patch("ai_video_factory.production_pipeline.call_model", return_value=response) as mock_model:
        script, source = _generate_script("Minecraft", summary, 30.0, "test-key")

    assert source == "model"
    assert script.splitlines()[0] == "The hidden truth"
    mock_model.assert_called_once()


def test_generate_script_falls_back_without_model_output():
    summary = {"topic": "Minecraft", "strongest_angle": "Betrayal", "emotion": "dramatic"}
    with patch("ai_video_factory.production_pipeline.call_model", return_value=""):
        script, source = _generate_script("Minecraft", summary, 30.0, "test-key")

    assert source == "template"
    assert script
