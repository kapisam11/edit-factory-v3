from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_video_factory.autonomous_youtube import (
    AutonomousConfig,
    AutonomousManager,
    AutonomousStore,
    _fact_review_pending,
    _rights_review_pending,
    _video_thinking_review_pending,
    PublicationIntegrityError,
    _extract_package,
)
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


def _insert_experiment_observation(
    store: AutonomousStore,
    *,
    job_id: str,
    family: str,
    title_variant: int,
    thumbnail_variant: int,
    analytics: dict,
) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO autonomous_jobs("
            "id,topic,state,created_at,updated_at,experiment_family,title_variant,thumbnail_variant,analytics_json"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                "A repeatable topic",
                "ANALYZED",
                timestamp,
                timestamp,
                family,
                title_variant,
                thumbnail_variant,
                json.dumps(analytics),
            ),
        )


def test_experiment_selector_uses_ctr_for_thumbnails_and_ctr_free_score_for_titles(tmp_path: Path):
    store = AutonomousStore(_config(tmp_path))
    family = "same-topic-family"
    _insert_experiment_observation(
        store, job_id="result-1", family=family, title_variant=1, thumbnail_variant=1,
        analytics={
            "score": 0.99,
            "title_score": 0.90,
            "thumbnailImpressions": 800,
            "thumbnailImpressionsClickThroughRate": 0.02,
        },
    )
    _insert_experiment_observation(
        store, job_id="result-2", family=family, title_variant=2, thumbnail_variant=2,
        analytics={
            "score": 0.40,
            "title_score": 0.40,
            "thumbnailImpressions": 700,
            "thumbnailImpressionsClickThroughRate": 0.07,
        },
    )
    _insert_experiment_observation(
        store, job_id="result-3", family=family, title_variant=3, thumbnail_variant=3,
        analytics={
            "score": 0.60,
            "title_score": 0.60,
            "thumbnailImpressions": 900,
            "thumbnailImpressionsClickThroughRate": 0.05,
        },
    )

    # The highest CTR belongs to thumbnail 2, while title 1 has the best
    # CTR-free performance score. The two choices must be independent.
    assert store._select_experiment_variants(family, 2, 3) == (1, 2)


def test_thumbnail_experiment_keeps_exploring_variants_below_impression_threshold(tmp_path: Path):
    store = AutonomousStore(_config(tmp_path))
    family = "low-sample-family"
    _insert_experiment_observation(
        store, job_id="small-1", family=family, title_variant=1, thumbnail_variant=1,
        analytics={
            "score": 0.8,
            "title_score": 0.8,
            "thumbnailImpressions": 500,
            "thumbnailImpressionsClickThroughRate": 0.03,
        },
    )
    _insert_experiment_observation(
        store, job_id="small-2", family=family, title_variant=2, thumbnail_variant=2,
        analytics={
            "score": 0.7,
            "title_score": 0.7,
            "thumbnailImpressions": 500,
            "thumbnailImpressionsClickThroughRate": 0.04,
        },
    )
    _insert_experiment_observation(
        store, job_id="small-3", family=family, title_variant=3, thumbnail_variant=3,
        analytics={
            "score": 0.3,
            "title_score": 0.3,
            "thumbnailImpressions": 25,
            "thumbnailImpressionsClickThroughRate": 0.99,
        },
    )

    # A 99% CTR from 25 impressions is not enough evidence to call a winner;
    # the least-measured arm is selected to continue exploration.
    assert store._select_experiment_variants(family, 1, 2) == (1, 3)


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


def test_required_factual_review_fails_closed_on_missing_evidence():
    assert _fact_review_pending({}, required=True)
    assert _fact_review_pending(
        {"claims_to_verify": [], "research_available": False},
        required=True,
    )
    assert not _fact_review_pending(
        {"claims_to_verify": [], "research_available": True},
        required=True,
    )
    assert not _fact_review_pending({}, required=False)


def test_rights_gate_fails_closed_for_missing_or_unresolved_evidence():
    assert _rights_review_pending(None)
    assert _rights_review_pending({"status": "review_required", "publish_blocked": True})
    assert _rights_review_pending(
        {"status": "not_declared", "publish_blocked": False, "requires_explicit_declaration": True}
    )
    assert not _rights_review_pending(
        {"status": "not_declared", "publish_blocked": False, "requires_explicit_declaration": False}
    )
    assert not _rights_review_pending(
        {"status": "cleared", "publish_blocked": False, "requires_explicit_declaration": True}
    )


def test_ai_publish_claim_requires_recorded_disclosure_review(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path))
    job_id = manager.enqueue("A synthetic media disclosure test", publish_requested=True)
    manager.store.update(
        job_id,
        state="SCHEDULED",
        approval="approved",
        scheduled_at="2000-01-01T00:00:00Z",
        package_dir=str(tmp_path / "package"),
        ai_generated=1,
        realistic_alteration=1,
        disclosure_reviewed=0,
    )

    assert manager.store.claim_publish(now="2026-10-08T12:00:00Z") is None
    assert manager.store.get(job_id)["state"] == "SCHEDULED"


def test_approval_records_disclosure_review_and_schedules_policy_review(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path))
    job_id = manager.enqueue("Review the disclosure before publishing")
    manager.store.update(
        job_id,
        state="POLICY_REVIEW",
        package_dir=str(tmp_path / "package"),
        ai_generated=1,
        realistic_alteration=1,
        disclosure_reviewed=0,
    )

    manager.approve(
        job_id,
        actor="test-reviewer",
        disclosure_acknowledged=True,
    )
    job = manager.store.get(job_id)
    assert job is not None
    assert job["state"] == "SCHEDULED"
    assert job["approval"] == "approved"
    assert job["disclosure_reviewed"] == 1

def test_ai_approval_requires_explicit_disclosure_acknowledgement(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path))
    job_id = manager.enqueue("Synthetic media needs disclosure review")
    manager.store.update(
        job_id,
        state="POLICY_REVIEW",
        ai_generated=1,
        realistic_alteration=1,
        disclosure_reviewed=0,
    )

    with pytest.raises(ValueError, match="disclosure acknowledgement"):
        manager.approve(job_id, actor="test-reviewer")

    job = manager.store.get(job_id)
    assert job is not None
    assert job["approval"] == "pending"
    assert job["disclosure_reviewed"] == 0


def test_publish_claim_respects_retry_backoff(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path))
    job_id = manager.enqueue("Backoff test")
    future = (datetime.now(timezone.utc).replace(microsecond=0)).isoformat().replace("+00:00", "Z")
    manager.store.update(
        job_id,
        state="SCHEDULED",
        approval="approved",
        scheduled_at="2000-01-01T00:00:00Z",
        next_attempt_at="2999-01-01T00:00:00Z",
    )

    assert manager.store.claim_publish(now=future) is None
    assert manager.store.get(job_id)["state"] == "SCHEDULED"


def test_publish_claim_enforces_daily_cap_atomically(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path, max_videos_per_day=1))
    published_id = manager.enqueue("Already published")
    waiting_id = manager.enqueue("Must stay queued")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manager.store.update(published_id, state="PUBLISHED", published_at=now)
    manager.store.update(
        waiting_id,
        state="SCHEDULED",
        approval="approved",
        scheduled_at="2000-01-01T00:00:00Z",
    )

    assert manager.store.claim_publish(now=now, max_videos_per_day=1) is None
    assert manager.store.get(waiting_id)["state"] == "SCHEDULED"


def test_package_extraction_rejects_path_traversal(tmp_path: Path):
    package = tmp_path / "package"
    metadata_dir = package / "upload" / "youtube_shorts"
    metadata_dir.mkdir(parents=True)
    (tmp_path / "outside.mp4").write_bytes(b"outside")
    (metadata_dir / "metadata.json").write_text(
        json.dumps({"files": {"video": "../../outside.mp4"}}),
        encoding="utf-8",
    )

    with pytest.raises(PublicationIntegrityError, match="escapes"):
        _extract_package(package)


def test_crash_recovery_requeues_abandoned_production_job(tmp_path: Path):
    manager = AutonomousManager(_config(tmp_path, max_retries=2))
    job_id = manager.enqueue("Interrupted production test")
    manager.store.update(
        job_id,
        state="PRODUCING",
        updated_at="2000-01-01T00:00:00Z",
    )

    recovered = manager.store.recover_stale_jobs(older_than_minutes=10)
    job = manager.store.get(job_id)
    assert recovered["production"] == 1
    assert job is not None
    assert job["state"] == "FAILED"
    assert job["retry_stage"] == "production"
    assert job["retry_count"] == 1
    assert job["next_attempt_at"]


def test_publish_blocks_metadata_changed_after_review(tmp_path: Path, monkeypatch):
    manager = AutonomousManager(_config(tmp_path))
    job_id = manager.enqueue("Publication integrity test")
    package = manager.config.output_dir / "autonomous" / job_id
    metadata_dir = package / "upload" / "youtube_shorts"
    metadata_dir.mkdir(parents=True)
    video = package / "video.mp4"
    video.write_bytes(b"approved fake video")
    metadata_path = metadata_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps({
            "files": {"video": "video.mp4"},
            "selected_title": "Publication integrity test",
            "description": "A sufficiently long, specific description for integrity testing.",
            "tags": [],
            "media_rights": {
                "status": "not_declared",
                "publish_blocked": False,
                "requires_explicit_declaration": False,
            },
        }),
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manager.store.update(
        job_id,
        state="SCHEDULED",
        approval="approved",
        scheduled_at="2000-01-01T00:00:00Z",
        package_dir=str(package),
        video_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
        metadata_sha256="0" * 64,
        published_at=None,
    )

    monkeypatch.delenv("AIVF_YOUTUBE_PRIVACY", raising=False)
    result = manager.publish_one()
    job = manager.store.get(job_id)
    assert result == job_id
    assert job is not None
    assert job["state"] == "POLICY_REVIEW"
    assert job["approval"] == "pending"
    assert "metadata changed" in job["error"]




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


def test_analytics_api_rows_are_mapped_by_column_headers():
    from ai_video_factory.autonomous_youtube import _first_analytics_row

    official_report = {
        "columnHeaders": [
            {"name": "views", "columnType": "METRIC", "dataType": "INTEGER"},
            {"name": "engagedViews", "columnType": "METRIC", "dataType": "INTEGER"},
            {"name": "averageViewPercentage", "columnType": "METRIC", "dataType": "FLOAT"},
        ],
        "rows": [[120, 84, 52.5]],
    }
    assert _first_analytics_row(official_report) == {
        "views": 120,
        "engagedViews": 84,
        "averageViewPercentage": 52.5,
    }
    assert _first_analytics_row({"rows": [{"views": 8, "engagedViews": 6}]}) == {
        "views": 8,
        "engagedViews": 6,
    }
    assert _first_analytics_row({"columnHeaders": [{"name": "views"}], "rows": [[1, 2]]}) == {}


@pytest.mark.parametrize(
    ("report", "enabled", "found", "expected"),
    [
        ({"status": "ready", "human_review_required": False}, True, True, False),
        ({"status": "not_configured", "human_review_required": False}, True, True, False),
        ({"status": "review_required", "human_review_required": True}, True, True, True),
        ({"status": "model_unavailable", "human_review_required": False}, True, True, True),
        ({"status": "invalid_report"}, True, True, True),
        ({}, True, False, True),
        (None, True, True, True),
        ({}, False, False, False),
    ],
)
def test_video_thinking_gate_fails_closed_for_weak_or_missing_reports(
    report, enabled, found, expected
):
    assert _video_thinking_review_pending(report, enabled=enabled, found=found) is expected
