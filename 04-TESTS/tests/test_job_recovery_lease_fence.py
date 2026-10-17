from __future__ import annotations

import time

from ai_video_factory import job_recovery


def test_stale_heartbeat_is_recovered_even_if_recorded_pid_looks_alive(monkeypatch) -> None:
    monkeypatch.setattr(job_recovery, "process_is_alive", lambda _pid: True)
    calls: list[tuple[str, str]] = []
    now = time.time()

    recovered = job_recovery.reconcile_job(
        job_id="job-hung-or-pid-reused",
        pid=4242,
        heartbeat_epoch=now - 120,
        last_seen_epoch=now - 120,
        timeout_seconds=30,
        mark_interrupted=lambda job_id, reason: calls.append((job_id, reason)),
    )

    assert recovered is True
    assert len(calls) == 1
    assert calls[0][0] == "job-hung-or-pid-reused"
    assert "heartbeat expired" in calls[0][1]
    assert "still appears alive" in calls[0][1]


def test_fresh_heartbeat_is_not_recovered_even_if_pid_is_dead(monkeypatch) -> None:
    monkeypatch.setattr(job_recovery, "process_is_alive", lambda _pid: False)
    calls: list[tuple[str, str]] = []
    now = time.time()

    recovered = job_recovery.reconcile_job(
        job_id="job-fresh",
        pid=4242,
        heartbeat_epoch=now,
        last_seen_epoch=now,
        timeout_seconds=30,
        mark_interrupted=lambda job_id, reason: calls.append((job_id, reason)),
    )

    assert recovered is False
    assert calls == []


def test_stale_heartbeat_recovers_when_worker_is_dead(monkeypatch) -> None:
    monkeypatch.setattr(job_recovery, "process_is_alive", lambda _pid: False)
    calls: list[tuple[str, str]] = []
    now = time.time()

    recovered = job_recovery.reconcile_job(
        job_id="job-dead",
        pid=4242,
        heartbeat_epoch=now - 120,
        last_seen_epoch=now - 120,
        timeout_seconds=30,
        mark_interrupted=lambda job_id, reason: calls.append((job_id, reason)),
    )

    assert recovered is True
    assert calls == [
        ("job-dead", "worker heartbeat expired and worker process is not running")
    ]
