from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_video_factory.autonomous_youtube import AutonomousConfig, AutonomousManager, AutonomousStore
from ai_video_factory.human_style_guard import (
    assess_package,
    assess_topic_diversity,
    sanitize_public_metadata,
)


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


def test_topic_diversity_blocks_near_duplicate_topics():
    assessment = assess_topic_diversity(
        "The hidden history of the Apollo 11 mission",
        ["A hidden history of Apollo 11", "Cooking tips for pasta"],
        min_similarity=0.65,
    )
    assert assessment["blocked"] is True
    assert assessment["max_recent_topic_similarity"] >= 0.65


def test_public_metadata_removes_generator_credit_only():
    cleaned = sanitize_public_metadata(
        "The history of AI",
        "A documentary about early AI. Powered by AI Video Factory.",
        ["AI history", "AIVF"],
    )
    assert cleaned["title"] == "The history of AI"
    assert "AI Video Factory" not in cleaned["description"]
    assert "AIVF" not in cleaned["tags"]
    assert "AI history" in cleaned["tags"]



def test_template_fallback_is_sentence_valid():
    from ai_video_factory.director import _validate_script_against_rules, VideoDirector

    director = VideoDirector(model_key=None)
    director.creative_brief = {
        "topic": "Test Topic",
        "hook": "A concrete hook",
        "strongest_angle": "A specific angle",
        "main_conflict": "A real conflict",
        "why_care": "A concrete consequence",
    }
    script = director.build_script({})
    assert _validate_script_against_rules(script, "A concrete hook") == []


def test_publish_claim_atomically_moves_one_job_to_uploading(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path, autonomous_publish=True, require_human_approval=False))
    job_id = manager.enqueue("A concrete topic", publish_requested=True)
    manager.store.update(
        job_id,
        state="SCHEDULED",
        approval="approved",
        scheduled_at="2000-01-01T00:00:00Z",
        package_dir=str(tmp_path / "package"),
    )
    claimed = manager.store.claim_publish(now="2026-10-08T12:00:00Z")
    assert claimed is not None
    assert claimed["id"] == job_id
    assert claimed["state"] == "UPLOADING"
    assert manager.store.get(job_id)["state"] == "UPLOADING"
    assert manager.store.claim_publish(now="2026-10-08T12:00:00Z") is None


def test_daily_cost_limit_accounts_for_generation_before_publish(tmp_path: Path):
    manager = AutonomousManager(
        _config(tmp_path, max_daily_cost_usd=1.0, estimated_cost_per_video_usd=1.0)
    )
    with pytest.raises(RuntimeError, match="daily cost limit"):
        manager._guard_limits()



def test_cost_reservation_is_atomic_and_counts_each_production_attempt(tmp_path: Path):
    manager = AutonomousManager(
        _config(tmp_path, max_daily_cost_usd=3.0, estimated_cost_per_video_usd=1.0)
    )
    job_ids = [manager.enqueue(f"Distinct topic {index}") for index in range(3)]

    first = manager.store.claim_production(
        estimated_cost_usd=1.0, max_daily_cost_usd=3.0
    )
    second = manager.store.claim_production(
        estimated_cost_usd=1.0, max_daily_cost_usd=3.0
    )
    assert first is not None and second is not None
    assert manager.store.daily_cost() == pytest.approx(2.0)

    with pytest.raises(RuntimeError, match="daily cost limit"):
        manager.store.claim_production(
            estimated_cost_usd=1.0, max_daily_cost_usd=3.0
        )
    states = {item["id"]: item["state"] for item in manager.store.list_jobs(limit=10)}
    assert states[job_ids[2]] == "IDEA"
    assert manager.store.daily_cost() == pytest.approx(2.0)


def test_per_video_estimated_cost_accumulates_across_retries(tmp_path: Path):
    manager = AutonomousManager(
        _config(tmp_path, max_daily_cost_usd=5.0, estimated_cost_per_video_usd=1.0)
    )
    job_id = manager.enqueue("A topic that requires a retry")

    first = manager.store.claim_production(
        estimated_cost_usd=1.0, max_daily_cost_usd=5.0
    )
    assert first is not None
    assert first["id"] == job_id
    assert first["actual_cost_usd"] == pytest.approx(1.0)

    manager.store.update(job_id, state="FAILED")
    second = manager.store.claim_production(
        estimated_cost_usd=1.0, max_daily_cost_usd=5.0
    )
    assert second is not None
    assert second["id"] == job_id
    assert second["actual_cost_usd"] == pytest.approx(2.0)
    assert manager.store.daily_cost() == pytest.approx(2.0)


def test_daily_cost_comes_from_ledger_not_mutable_job_updated_at(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path))
    job_id = manager.enqueue("Untouched cost-ledger test")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manager.store.update(job_id, actual_cost_usd=4.0, updated_at=now)

    assert manager.store.daily_cost() == pytest.approx(0.0)


def test_human_quality_guard_rejects_reused_title_template():
    assessment = assess_package(
        script=(
            "Apollo 11 took a dangerous turn after a small decision. "
            "The crew had seconds to react, and the consequence was real."
        ),
        title="Why Apollo 11 still matters",
        description="A specific history of the mission and the decision that followed.",
        recent_titles=["Why Challenger still matters"],
    )
    assert assessment.publish_blocked is True
    assert any("template" in reason or "similar" in reason for reason in assessment.reasons)


def test_stale_schedule_is_moved_to_policy_review(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path, stale_schedule_days=1))
    job_id = manager.enqueue("Old scheduled topic")
    manager.store.update(
        job_id,
        state="SCHEDULED",
        approval="approved",
        scheduled_at="2000-01-01T00:00:00Z",
        updated_at="2000-01-01T00:00:00Z",
    )
    manager.store.prune()
    job = manager.store.get(job_id)
    assert job is not None
    assert job["state"] == "POLICY_REVIEW"
    assert job["approval"] == "pending"


def test_automatic_backup_writes_a_non_secret_config_snapshot(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path))
    archive = manager._maybe_backup()
    assert archive
    snapshot = manager.config.output_dir / "autonomous-config.json"
    assert snapshot.exists()
    payload = snapshot.read_text(encoding="utf-8")
    assert "max_videos_per_day" in payload
    assert "YOUTUBE_TOKEN_PATH" not in payload
