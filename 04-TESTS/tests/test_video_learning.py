from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_video_factory.learning_recommender import recommend
from ai_video_factory.video_learning import (
    add_manual_rating,
    eligible_examples,
    load_preference_model,
    observed_reward,
    predict_reward,
    train_preference_model,
)
from ai_video_factory.advanced_intelligence import CaptionCue, write_ass_captions
from ai_video_factory.complete_factory import update_learning_history


def _history(count: int = 12) -> list[dict]:
    records = []
    for index in range(count):
        retention = 0.35 + (index / max(1, count - 1)) * 0.55
        engagement = 0.005 + (index / max(1, count - 1)) * 0.075
        cuts = 8.0 + index * 1.7
        records.append({
            "topic": f"sample topic {index}",
            "platform": "youtube_shorts",
            "content_type": "short_video",
            "cuts_per_minute": cuts,
            "avg_shot_duration": 60.0 / cuts,
            "hook_duration": 1.0 + (index % 4) * 0.25,
            "music_energy": 0.2 + (index % 5) * 0.15,
            "caption_style": ("karaoke", "bold", "minimal")[index % 3],
            "voice": ("voice_a", "voice_b")[index % 2],
            "music_style": ("dramatic", "emotional")[index % 2],
            "edit_type": ("dramatic", "storytelling", "funny")[index % 3],
            "views": 1000 + index * 100,
            "likes": int((1000 + index * 100) * engagement),
            "comments": 3 + index,
            "engagement_rate": engagement,
            "retention": retention,
        })
    return records


def test_unobserved_placeholder_scores_do_not_become_training_labels() -> None:
    rows = [{
        "cuts_per_minute": 12.0,
        "avg_shot_duration": 2.5,
        "hook_duration": 2.0,
        "music_energy": 0.5,
        "performance_score": 0.0,
    }]
    assert observed_reward(rows[0]) is None
    assert eligible_examples(rows) == []


def test_observed_reward_combines_retention_and_engagement() -> None:
    score = observed_reward({"retention": 0.8, "engagement_rate": 0.05, "views": 2000})
    assert score == pytest.approx(0.71)
    assert observed_reward({"averageViewPercentage": 75.0, "views": 800}) == pytest.approx(0.75)
    assert observed_reward({"user_rating": 5}) == 1.0
    assert observed_reward({"user_rating": 1}) == 0.0


def test_preference_model_retrains_and_persists_coefficients(tmp_path: Path) -> None:
    history = _history()
    target = tmp_path / "state" / "video_preference_model.json"

    model = train_preference_model(history, model_path=target, min_samples=6)

    assert model["status"] == "trained"
    assert model["algorithm"] == "weighted_ridge_regression"
    assert model["samples"] == len(history)
    assert len(model["coefficients"]) == len(model["feature_names"])
    assert target.is_file()
    loaded = load_preference_model(target)
    assert loaded is not None
    assert loaded["trained_at"] == model["trained_at"]
    prediction = predict_reward(loaded, history[-1])
    assert prediction is not None
    assert 0.0 <= prediction <= 1.0


def test_recommender_uses_trained_model_to_rank_tested_edit_profiles(tmp_path: Path) -> None:
    history = _history()
    model_path = tmp_path / "video_preference_model.json"

    result = recommend(
        {
            "platform": "youtube_shorts",
            "content_type": "short_video",
            "cuts_per_minute": 12.0,
            "avg_shot_duration": 2.5,
            "hook_duration": 2.0,
            "music_energy": 0.5,
        },
        history,
        defaults={"caption_style": "karaoke"},
        model_path=str(model_path),
    )

    assert result.learning["status"] == "trained"
    assert result.learning["candidate_count"] == len(history)
    assert 0.0 <= result.learning["predicted_reward"] <= 1.0
    assert result.settings["cuts_per_minute"] in {row["cuts_per_minute"] for row in history}
    assert model_path.is_file()
    # Learning output is provenance only; model weights are not language-model weights.
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    assert payload["algorithm"] == "weighted_ridge_regression"
    assert "script" not in payload


def test_model_waits_for_enough_outcomes_and_keeps_previous_model(tmp_path: Path) -> None:
    history = _history()
    path = tmp_path / "video_preference_model.json"
    fitted = train_preference_model(history, model_path=path, min_samples=6)
    assert fitted["status"] == "trained"

    warming = train_preference_model(history[:2], model_path=path, min_samples=6)
    assert warming["status"] == "trained"
    assert warming["samples"] == len(history)
    assert warming["latest_history_samples"] == 2
    assert path.is_file()


def test_manual_rating_is_attached_to_the_matching_output_package(tmp_path: Path) -> None:
    package = tmp_path / "output" / "job-001"
    package.mkdir(parents=True)
    history_path = tmp_path / "state" / "learning_history.json"
    history_path.parent.mkdir()
    rows = _history(2)
    rows[0]["package_dir"] = str(package)
    rows[1]["package_dir"] = str(tmp_path / "other")
    history_path.write_text(json.dumps(rows), encoding="utf-8")

    rated = add_manual_rating(
        history_path,
        package_dir=str(package),
        rating=5,
        note="Great pacing and ending.",
    )

    assert rated["user_rating"] == 5
    assert rated["user_feedback"] == "Great pacing and ending."
    stored = json.loads(history_path.read_text(encoding="utf-8"))
    assert stored[0]["user_rating"] == 5
    assert "user_rating" not in stored[1]


def test_manual_rating_rejects_invalid_rating_and_unknown_package(tmp_path: Path) -> None:
    history_path = tmp_path / "learning_history.json"
    history_path.write_text(json.dumps(_history(1)), encoding="utf-8")

    with pytest.raises(ValueError, match="rating must be"):
        add_manual_rating(history_path, package_dir="/missing", rating=6)
    with pytest.raises(KeyError, match="no learning history record"):
        add_manual_rating(history_path, package_dir="/missing", rating=5)

def test_learned_caption_style_changes_rendered_ass_preset(tmp_path: Path) -> None:
    cue = CaptionCue(text="A real payoff", start=0.0, end=1.2, x=0.5, y=0.78)
    bold_path = tmp_path / "bold.ass"
    minimal_path = tmp_path / "minimal.ass"

    write_ass_captions([cue], str(bold_path), caption_style="bold")
    write_ass_captions([cue], str(minimal_path), caption_style="minimal")

    bold_style = next(line for line in bold_path.read_text(encoding="utf-8").splitlines() if line.startswith("Style: Default"))
    minimal_style = next(line for line in minimal_path.read_text(encoding="utf-8").splitlines() if line.startswith("Style: Default"))
    assert bold_style != minimal_style
    assert ",72," in bold_style
    assert ",52," in minimal_style

def test_youtube_analytics_become_trainable_settings_and_reward(tmp_path: Path) -> None:
    package = tmp_path / "output" / "video-001"
    primary = package / "primary"
    primary.mkdir(parents=True)
    (primary / "metrics.json").write_text(json.dumps({
        "cuts_per_minute": 16.0,
        "segment_count": 8,
        "actual_duration_seconds": 30.0,
    }), encoding="utf-8")
    (primary / "metadata.json").write_text(json.dumps({
        "recommendation": {
            "settings": {
                "cuts_per_minute": 16.0,
                "avg_shot_duration": 3.75,
                "hook_duration": 1.25,
                "music_energy": 0.65,
                "caption_style": "minimal",
                "voice": "en-US-AriaNeural",
                "music_style": "emotional",
            }
        }
    }), encoding="utf-8")
    (primary / "video_thinking.json").write_text(json.dumps({
        "plan": {"emotion": "dramatic"}
    }), encoding="utf-8")
    history_path = tmp_path / "state" / "learning_history.json"

    record = update_learning_history(
        str(history_path),
        topic="test topic",
        platform="youtube_shorts",
        package_dir=str(package),
        metrics={},
        performance_metrics={
            "views": 1000,
            "likes": 70,
            "comments": 10,
            "averageViewPercentage": 75.0,
        },
    )

    assert record["cuts_per_minute"] == 16.0
    assert record["avg_shot_duration"] == 3.75
    assert record["hook_duration"] == 1.25
    assert record["music_energy"] == 0.65
    assert record["caption_style"] == "minimal"
    assert record["voice"] == "en-US-AriaNeural"
    assert record["music_style"] == "emotional"
    assert record["edit_type"] == "dramatic"
    assert record["retention"] == 0.75
    assert record["engagement_rate"] == 0.09
    assert observed_reward(record) == pytest.approx(0.795)
