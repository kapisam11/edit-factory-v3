from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_video_factory.media_limits import MediaLimits, validate_input_file
from dashboard_store import DashboardStore, JobAdmissionError


def _media_file(path: Path, payload: bytes = b"demo") -> Path:
    path.write_bytes(payload)
    return path


def test_corrupt_mp4_is_rejected_before_queue(tmp_path, monkeypatch):
    path = _media_file(tmp_path / "corrupt.mp4")
    with pytest.raises(Exception, match="signature|media"):
        validate_input_file(
            path,
            suffix=".mp4",
            limits=MediaLimits(max_input_bytes=1024),
        )


def test_oversized_input_is_rejected_before_ffprobe(tmp_path):
    path = _media_file(tmp_path / "large.mp4", b"x" * 2048)
    with pytest.raises(Exception, match="size"):
        validate_input_file(
            path,
            suffix=".mp4",
            limits=MediaLimits(max_input_bytes=1024),
        )


def test_resource_admission_rejects_when_reserved_capacity_is_full(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()
    store.insert_job(
        "job-1",
        "first",
        {},
        resource_units=80,
        resource_capacity_units=100,
    )
    with pytest.raises(JobAdmissionError, match="resource capacity"):
        store.insert_job(
            "job-2",
            "second",
            {},
            resource_units=30,
            resource_capacity_units=100,
        )


def test_disk_admission_preserves_required_headroom(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()
    with pytest.raises(JobAdmissionError, match="disk"):
        store.insert_job(
            "job-1",
            "disk",
            {},
            reserved_disk_bytes=90,
            available_disk_bytes=100,
            resource_units=1,
        )


def test_cancel_after_completion_is_refused(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()
    store.insert_job("job-1", "topic", {})
    assert store.claim_job("job-1") is True
    assert store.update_job("job-1", status="done", step="Complete") == 1
    assert store.update_job_if_status("job-1", ("done",), status="cancelled") == 0


def test_stale_attempt_cannot_publish(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()
    store.insert_job("job-1", "topic", {})
    assert store.claim_job(
        "job-1",
        attempt_id="attempt-a",
        worker_id="worker-a",
        lease_token="lease-a",
        workspace_dir=str(tmp_path / "attempt-a"),
    )
    assert store.update_job_if_owned(
        "job-1",
        "attempt-a",
        "lease-a",
        status="error",
        error="worker died",
    )
    assert store.retry_job("job-1", max_attempts=2) == 1
    assert store.claim_job(
        "job-1",
        attempt_id="attempt-b",
        worker_id="worker-b",
        lease_token="lease-b",
        workspace_dir=str(tmp_path / "attempt-b"),
    )
    assert store.can_publish_attempt("job-1", "attempt-a", "lease-a") is False
    assert store.can_publish_attempt("job-1", "attempt-b", "lease-b") is True


def test_fifty_unique_submissions_do_not_exceed_single_transaction_races(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()

    def create(index: int):
        try:
            store.insert_job(
                f"job-{index}",
                f"topic-{index}",
                {},
                max_queued_jobs=100,
                principal=f"principal-{index % 5}",
                principal_limit=100,
            )
            return True
        except Exception:
            return False

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(create, range(50)))

    assert sum(results) == 50
    assert len(store.list_jobs(100)) == 50
