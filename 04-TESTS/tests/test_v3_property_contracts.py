from __future__ import annotations

import random

import pytest

from ai_video_factory.retry_policy import idempotency_key
from ai_video_factory.v3_contracts import V3Request
from ai_video_factory.v3_engine import PLATFORM_PROFILES, V3Config, V3Blueprint, create_v3_blueprint
from ai_video_factory.v3_retention import evaluate_retention_editorial_fit


def test_v3_blueprint_round_trip_property():
    rng = random.Random(20261002)
    platforms = tuple(PLATFORM_PROFILES)
    for _ in range(40):
        platform = rng.choice(platforms)
        profile = PLATFORM_PROFILES[platform]
        max_seconds = float(profile["max_seconds"] or 60)
        target = round(rng.uniform(8.0, min(60.0, max_seconds)), 2)
        blueprint = create_v3_blueprint(
            "Minecraft clutch survival",
            context="A player survives a difficult moment",
            config=V3Config(
                target_seconds=target,
                platform=platform,
                audience="gaming viewers",
                bpm=rng.randint(90, 180),
            ),
        )
        restored = V3Blueprint.from_dict(blueprint.to_dict())
        assert restored == blueprint
        assert restored.duration == pytest.approx(target, abs=0.01)
        assert all(
            a.end <= b.start + 0.01
            for a, b in zip(restored.clip_plan, restored.clip_plan[1:])
        )
        assert all(
            earlier.time < later.time
            for earlier, later in zip(restored.retention_map, restored.retention_map[1:])
        )


def test_v3_request_uses_one_canonical_config_validator(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture")
    request = V3Request(
        input_video=str(source),
        topic="Minecraft",
        package_dir=str(tmp_path / "package"),
        target_seconds=30,
        platform="youtube_shorts",
        audience="gaming",
        bpm=120,
    )
    request.validate()
    with pytest.raises(ValueError):
        V3Config(target_seconds=61, platform="youtube_shorts").validate()


def test_idempotency_is_stable_for_mapping_order():
    first = {"topic": "Minecraft", "target_seconds": 30, "nested": {"a": 1, "b": 2}}
    second = {"nested": {"b": 2, "a": 1}, "target_seconds": 30, "topic": "Minecraft"}
    assert idempotency_key(first) == idempotency_key(second)


def test_retention_editorial_contract_rejects_overcrowded_effects():
    events = [
        {"time": 0.0, "kind": "zoom", "instruction": "Open with the strongest visual moment"},
        {"time": 0.2, "kind": "zoom", "instruction": "Add another visual change here"},
        {"time": 0.4, "kind": "zoom", "instruction": "Add another visual change here"},
        {"time": 2.0, "kind": "motion", "instruction": "Change motion with the story beat"},
    ]
    report = evaluate_retention_editorial_fit(
        events,
        duration=8.0,
        target_interval=2.0,
        clip_boundaries=(2.0,),
    )
    assert report.passed is False
    assert report.checks["not_overcrowded"] is False
