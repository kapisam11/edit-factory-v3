from __future__ import annotations

import math
from pathlib import Path

from ai_video_factory.knowledge_v3 import (
    EngagementPredictor,
    FeedbackRecord,
    VideoFeatures,
)


def _feedback(index: int, features: VideoFeatures, score: float) -> FeedbackRecord:
    return FeedbackRecord(
        package_id=f"video-{index}",
        timestamp=f"2026-10-{index:02d}T12:00:00Z",
        engagement_score=score,
        features=features,
    )


def _features(
    *,
    avg_shot_duration: float,
    cuts_per_minute: float,
    filters_used: dict[str, bool] | None = None,
) -> VideoFeatures:
    return VideoFeatures(
        topic="test topic",
        filter_count=len([name for name, enabled in (filters_used or {}).items() if enabled]),
        avg_shot_duration=avg_shot_duration,
        cuts_per_minute=cuts_per_minute,
        music_bpm=110.0,
        voiceover_wpm=125.0,
        render_time_seconds=4.0,
        has_voiceover=True,
        has_music=True,
        thumbnail_style="default",
        shot_variety_ratio=0.4,
        filters_used=filters_used or {},
    )


def test_engagement_predictor_normalization_uses_observation_count_and_resets(tmp_path: Path) -> None:
    model = EngagementPredictor(str(tmp_path / "engagement_model.json"))
    records = [
        _feedback(1, _features(avg_shot_duration=1.0, cuts_per_minute=10.0, filters_used={"sparkle": True}), 0.2),
        _feedback(2, _features(avg_shot_duration=2.0, cuts_per_minute=20.0, filters_used={}), 0.5),
        _feedback(3, _features(avg_shot_duration=5.0, cuts_per_minute=30.0, filters_used={"sparkle": True}), 0.9),
    ]

    model.train(records)

    expected_mean = 8.0 / 3.0
    expected_std = math.sqrt(26.0) / 3.0
    assert math.isclose(model.feature_means["avg_shot_duration"], expected_mean, rel_tol=1e-9)
    assert math.isclose(model.feature_stds["avg_shot_duration"], expected_std, rel_tol=1e-9)
    assert math.isclose(model.feature_means["filter_sparkle"], 2.0 / 3.0, rel_tol=1e-9)
    assert math.isclose(model.feature_stds["filter_sparkle"], math.sqrt(2.0) / 3.0, rel_tol=1e-9)

    first_means = dict(model.feature_means)
    first_stds = dict(model.feature_stds)
    model.train(records)
    assert model.feature_means == first_means
    assert model.feature_stds == first_stds


def test_engagement_predictor_normalizes_absent_sparse_features_as_zero(tmp_path: Path) -> None:
    model = EngagementPredictor(str(tmp_path / "engagement_model.json"))
    records = [
        _feedback(1, _features(avg_shot_duration=1.0, cuts_per_minute=10.0, filters_used={"sparkle": True}), 0.2),
        _feedback(2, _features(avg_shot_duration=2.0, cuts_per_minute=20.0, filters_used={}), 0.5),
        _feedback(3, _features(avg_shot_duration=3.0, cuts_per_minute=30.0, filters_used={"sparkle": True}), 0.9),
    ]
    model.train(records)

    normalized = model._normalize({"avg_shot_duration": 2.0, "cuts_per_minute": 20.0})
    expected = (0.0 - model.feature_means["filter_sparkle"]) / model.feature_stds["filter_sparkle"]
    assert math.isclose(normalized["filter_sparkle"], expected, rel_tol=1e-9)
    prediction = model.predict(_features(avg_shot_duration=2.0, cuts_per_minute=20.0))
    assert math.isfinite(prediction)
    assert 0.0 <= prediction <= 1.0


def test_engagement_predictor_constant_features_have_stable_scale(tmp_path: Path) -> None:
    model = EngagementPredictor(str(tmp_path / "engagement_model.json"))
    records = [
        _feedback(i, _features(avg_shot_duration=2.0, cuts_per_minute=12.0), 0.4 + i * 0.1)
        for i in (1, 2, 3)
    ]
    model.train(records)

    assert model.feature_means["avg_shot_duration"] == 2.0
    assert model.feature_stds["avg_shot_duration"] == 1.0
    assert math.isfinite(model.predict(_features(avg_shot_duration=2.0, cuts_per_minute=12.0)))


def test_legacy_engagement_model_is_not_loaded_with_unverified_normalization(tmp_path: Path) -> None:
    path = tmp_path / "engagement_model.json"
    path.write_text(
        '{"weights":{"avg_shot_duration":100.0},"bias":0.95,'
        '"means":{"avg_shot_duration":999.0},"stds":{"avg_shot_duration":0.000001}}',
        encoding="utf-8",
    )

    model = EngagementPredictor(str(path))

    assert model.weights == {}
    assert model.bias == 0.5
    assert model.feature_means == {}
    assert model.feature_stds == {}
