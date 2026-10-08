from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_video_factory.autonomous_youtube import AutonomousConfig, AutonomousManager, AutonomousStore
from ai_video_factory.human_style_guard import assess_package, sanitize_public_metadata


def _config(tmp_path: Path, **overrides):
    values = dict(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "output",
        queue_db=tmp_path / "state" / "autonomous.sqlite",
        max_videos_per_day=2,
        max_queue_size=30,
        max_consecutive_failures=3,
        max_daily_cost_usd=5.0,
        estimated_cost_per_video_usd=1.0,
        max_retries=2,
        retry_base_seconds=1,
        retry_max_seconds=10,
        publish_times_utc=("12:00", "18:00"),
        autonomous_publish=False,
        require_human_approval=True,
    )
    values.update(overrides)
    return AutonomousConfig(**values)


def test_human_style_guard_rejects_generic_ai_sounding_script():
    assessment = assess_package(
        script=(
            "Today we're going to talk about this amazing story. "
            "You won't believe what happened. This changed everything. "
            "Wait until the end because the ending is absolutely insane."
        ),
        title="You Won't Believe What Happened",
        description="In this video we are going to explore a shocking story.",
    )
    assert assessment.publish_blocked is True
    assert assessment.reasons


def test_public_metadata_removes_factory_vendor_credit_but_keeps_topic_text():
    cleaned = sanitize_public_metadata(
        "The history of AI",
        "A concise story. Created with AI and Edit Factory.\nThe subject itself is AI history.",
        ["history", "AI", "Edit Factory"],
    )
    assert cleaned["title"] == "The history of AI"
    assert "Edit Factory" not in cleaned["description"]
    assert "Created with AI" not in cleaned["description"]
    assert "AI history" in cleaned["description"]
    assert "Edit Factory" not in cleaned["tags"]
    assert "#editfactory" not in cleaned["description"].lower()
    assert "AI Video Factory" not in cleaned["description"]


def test_autonomous_queue_persists_schedule_platform_and_options(tmp_path: Path):
    store = AutonomousStore(_config(tmp_path))
    job_id = store.enqueue(
        "A specific documentary topic",
        platform="youtube",
        scheduled_at="2026-10-09T12:00:00Z",
        options={"target_seconds": 60},
    )
    job = store.get(job_id)
    assert job is not None
    assert job["state"] == "IDEA"
    assert job["platform"] == "youtube"
    assert job["options_json"] == '{"target_seconds": 60}'
    assert job["published_at"] is None


def test_daily_publish_limit_is_a_hard_stop(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path, max_videos_per_day=1))
    job_id = manager.enqueue("Topic")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manager.store.update(job_id, state="PUBLISHED", published_at=now)
    with pytest.raises(RuntimeError, match="daily upload limit"):
        manager._guard_limits()


def test_failure_retry_uses_exponential_backoff_without_reproducing_publish_job(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path, max_retries=2, retry_base_seconds=2, retry_max_seconds=10))
    job_id = manager.enqueue("Topic")
    manager.store.update(job_id, state="SCHEDULED", approval="approved", scheduled_at="2000-01-01T00:00:00Z")
    job = manager.store.get(job_id)
    assert job is not None
    manager._record_failure(job, RuntimeError("temporary upload failure"), stage="publish")
    updated = manager.store.get(job_id)
    assert updated is not None
    assert updated["state"] == "SCHEDULED"
    assert updated["retry_stage"] == "publish"
    assert int(updated["retry_count"]) == 1
    assert updated["next_attempt_at"]


def test_emergency_stop_is_persistent(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path))
    manager.emergency_stop("test")
    assert manager.status()["emergency_stop"] is True
    with pytest.raises(RuntimeError, match="emergency stop"):
        manager._guard_limits()


def test_queue_assigns_stable_experiment_variants(tmp_path: Path):
    store = AutonomousStore(_config(tmp_path))
    first = store.enqueue("A topic with a concrete angle")
    second = store.enqueue("A different concrete angle")
    one = store.get(first)
    two = store.get(second)
    assert one is not None and two is not None
    assert one["experiment_family"]
    assert int(one["title_variant"]) in {1, 2, 3}
    assert int(one["thumbnail_variant"]) in {1, 2, 3}
    assert one["experiment_family"] != two["experiment_family"]
