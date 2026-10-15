from __future__ import annotations

import json
from pathlib import Path

from ai_video_factory.complete_factory import update_learning_history
from ai_video_factory.learning_recommender import recommend
from ai_video_factory.video_learning import train_preference_model


def _records(*, valorant_count: int, total: int = 12) -> list[dict]:
    rows = []
    for index in range(total):
        game = "Valorant" if index < valorant_count else "Minecraft"
        rows.append({
            "topic": f"{game} test video {index}",
            "platform": "youtube_shorts",
            "content_type": "gaming",
            "format": "youtube_shorts:short",
            "game": game,
            "duration_seconds": 30 + index,
            "hook_type": "decisive_action" if index % 2 else "instructional_problem",
            "commentary_style": "clutch" if index % 2 else "strategy",
            "cuts_per_minute": 12 + index,
            "avg_shot_duration": 60 / (12 + index),
            "hook_duration": 1 + (index % 3) * 0.3,
            "music_energy": 0.3 + (index % 4) * 0.1,
            "caption_style": "bold" if index % 2 else "minimal",
            "voice": "voice_a",
            "music_style": "focused",
            "edit_type": "dramatic" if index % 2 else "documentary",
            "views": 1000 + index * 100,
            "likes": 80 + index * 5,
            "comments": 10 + index,
            "retention": 0.55 + (index % 5) * 0.06,
        })
    return rows


def test_preference_model_encodes_game_format_duration_hook_and_commentary(tmp_path: Path) -> None:
    rows = _records(valorant_count=6)
    model = train_preference_model(rows, model_path=tmp_path / "model.json")

    assert model["status"] == "trained"
    assert model["schema_version"] == 2
    assert "duration_seconds" in model["numeric_features"]
    for field in ("game", "format", "hook_type", "commentary_style"):
        assert field in model["categorical_features"]
        assert field in model["categories"]
    assert "Valorant" in model["categories"]["game"]
    assert "youtube_shorts:short" in model["categories"]["format"]


def test_too_few_video_outcomes_for_a_game_uses_baseline_and_says_why(tmp_path: Path) -> None:
    rows = _records(valorant_count=2)
    defaults = {
        "cuts_per_minute": 12.0,
        "avg_shot_duration": 2.5,
        "hook_duration": 2.0,
        "music_energy": 0.5,
        "caption_style": "minimal",
    }

    result = recommend(
        {
            "platform": "youtube_shorts",
            "content_type": "gaming",
            "format": "youtube_shorts:short",
            "game": "Valorant",
            "duration_seconds": 35.0,
            "hook_type": "decisive_action",
            "commentary_style": "clutch",
        },
        rows,
        defaults=defaults,
        model_path=str(tmp_path / "model.json"),
    )

    assert result.settings == defaults
    assert result.confidence <= 0.15
    assert result.evidence_count == 2
    assert result.learning["contextual_recommendation"] == "insufficient_game_specific_data"
    assert "Only 2 measured/rated videos for Valorant" in result.reason


def test_game_specific_recommendation_only_ranks_seen_configurations_and_caps_small_sample_confidence(tmp_path: Path) -> None:
    rows = _records(valorant_count=4)
    result = recommend(
        {
            "platform": "youtube_shorts",
            "content_type": "gaming",
            "format": "youtube_shorts:short",
            "game": "Valorant",
            "duration_seconds": 35.0,
            "hook_type": "decisive_action",
            "commentary_style": "clutch",
        },
        rows,
        defaults={"caption_style": "minimal"},
        model_path=str(tmp_path / "model.json"),
    )

    assert result.evidence_count <= 4
    assert result.confidence <= 0.40
    assert result.learning["context_game_sample_count"] == 4
    assert result.learning["contextual_recommendation"] == "game_specific_candidates"
    assert "Only 4 measured/rated videos for Valorant" in result.learning["uncertainty"]


def test_learning_history_persists_explicit_gaming_context(tmp_path: Path) -> None:
    package = tmp_path / "output" / "valorant-clutch"
    package.mkdir(parents=True)
    (package / "gaming_profile.json").write_text(json.dumps({
        "enabled": True,
        "game_name": "Valorant",
        "creator_angle": "clutch",
    }), encoding="utf-8")
    (package / "v3_blueprint.json").write_text(json.dumps({
        "hooks": [{"text": "The last flash changed the fight"}],
    }), encoding="utf-8")
    (package / "metrics.json").write_text(json.dumps({
        "actual_duration_seconds": 35.0,
        "segment_count": 9,
        "cuts_per_minute": 15.4,
    }), encoding="utf-8")

    history_path = tmp_path / "state" / "learning_history.json"
    record = update_learning_history(
        str(history_path),
        topic="Valorant: the delayed flash",
        platform="youtube_shorts",
        package_dir=str(package),
        metrics={},
        performance_metrics={
            "views": 1200,
            "likes": 95,
            "comments": 12,
            "averageViewPercentage": 76.0,
        },
        video_id="yt-game-context-1",
        published_at="2026-10-01T12:00:00Z",
    )

    assert record["game"] == "Valorant"
    assert record["format"] == "youtube_shorts:short"
    assert record["duration_seconds"] == 35.0
    assert record["hook_type"] == "decisive_action"
    assert record["commentary_style"] == "clutch"
    assert record["hook"] == "The last flash changed the fight"
    stored = json.loads(history_path.read_text(encoding="utf-8"))
    assert stored[0]["game"] == "Valorant"
