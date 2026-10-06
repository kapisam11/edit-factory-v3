from __future__ import annotations

import sqlite3
import time

import pytest

from dashboard_cache import HybridCache
from dashboard_store import DashboardStore, JobRetryNotAllowed


def _create_schema(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                step TEXT NOT NULL,
                params TEXT NOT NULL,
                pkg_dir TEXT,
                error TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            );
            CREATE TABLE rate_limits (client_ip TEXT NOT NULL, ts REAL NOT NULL);
            """
        )


def test_dashboard_store_indexes_and_job_roundtrip(tmp_path):
    db = tmp_path / "jobs.db"
    _create_schema(db)
    store = DashboardStore(db)
    store.ensure_indexes()

    with store.connect() as conn:
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list(jobs)")}
        rate_indexes = {row["name"] for row in conn.execute("PRAGMA index_list(rate_limits)")}
    assert "idx_jobs_status_created" in indexes
    assert "idx_jobs_status_updated" in indexes
    assert "idx_rate_limits_client_ip_ts" in rate_indexes

    with store.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, topic, status, step, params, pkg_dir, error, retry_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            ("job-1", "topic", "error", "failed", "{}", None, "broken", 0),
        )
    assert store.get_job("job-1")["status"] == "error"
    assert store.update_job("job-1", status="queued", step="waiting", error=None) == 1
    assert store.get_job("job-1")["status"] == "queued"
    assert store.update_job_if_status("job-1", ("queued",), status="running") == 1
    assert store.update_job_if_status("job-1", ("queued",), status="done") == 0


def test_dashboard_store_list_limit_is_bounded(tmp_path):
    db = tmp_path / "jobs.db"
    _create_schema(db)
    store = DashboardStore(db)
    store.ensure_indexes()
    for index in range(3):
        store.insert_job(f"job-{index}", f"topic-{index}", {"topic": f"topic-{index}"})
    assert len(store.list_jobs(999999)) == 3
    assert len(store.list_jobs(-10)) == 1
    with pytest.raises(ValueError, match="limit"):
        store.list_jobs("not-an-int")


def test_hybrid_cache_expires_and_deletes():
    cache = HybridCache()
    cache.set_json("k", {"value": 1}, 0.1)
    assert cache.get_json("k") == {"value": 1}
    time.sleep(0.15)
    assert cache.get_json("k") is None
    cache.set_json("k", {"value": 2}, 10)
    cache.delete("k")
    assert cache.get_json("k") is None



def test_dashboard_store_rejects_illegal_status_transition(tmp_path):
    db = tmp_path / "jobs.db"
    _create_schema(db)
    store = DashboardStore(db)
    store.ensure_indexes()
    store.insert_job("job-1", "topic", {})
    assert store.claim_job("job-1") is True
    assert store.claim_job("job-1") is False
    assert store.update_job("job-1", status="done") == 1
    with pytest.raises(ValueError, match="Invalid job status transition"):
        store.update_job("job-1", status="running")


def test_dashboard_store_claim_is_atomic(tmp_path):
    db = tmp_path / "jobs.db"
    _create_schema(db)
    store_a = DashboardStore(db)
    store_b = DashboardStore(db)
    store_a.ensure_indexes()
    store_a.insert_job("job-1", "topic", {})
    results = []
    import threading

    def claim(store):
        results.append(store.claim_job("job-1"))

    threads = [threading.Thread(target=claim, args=(store_a,)), threading.Thread(target=claim, args=(store_b,))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True]



def test_dashboard_store_retry_policy_is_bounded_and_rejects_deterministic_failure(tmp_path):
    db = tmp_path / "jobs.db"
    _create_schema(db)
    store = DashboardStore(db)
    store.ensure_indexes()
    store.insert_job("job-1", "topic", {})
    assert store.claim_job("job-1") is True
    store.update_job("job-1", status="error", error="provider returned 429")
    assert store.retry_job("job-1", max_attempts=1) == 1
    assert store.get_job("job-1")["retry_count"] == 1
    store.update_job("job-1", status="error", error="invalid configuration")
    with pytest.raises(JobRetryNotAllowed, match="deterministic|maximum"):
        store.retry_job("job-1", max_attempts=1)


def test_dashboard_store_idempotency_is_atomic_and_bounded(tmp_path):
    db = tmp_path / "jobs.db"
    _create_schema(db)
    store = DashboardStore(db)
    store.ensure_indexes()

    assert store.insert_job_idempotent(
        "job-idem-1",
        "topic",
        {"topic": "topic", "_principal": "alice"},
        principal="alice",
        idempotency_key="request-1",
        request_hash="hash-1",
    ) == ("job-idem-1", True)

    assert store.insert_job_idempotent(
        "job-idem-2",
        "topic",
        {"topic": "topic", "_principal": "alice"},
        principal="alice",
        idempotency_key="request-1",
        request_hash="hash-1",
    ) == ("job-idem-1", False)

    from dashboard_store import IdempotencyConflict
    with pytest.raises(IdempotencyConflict):
        store.insert_job_idempotent(
            "job-idem-3",
            "different topic",
            {"topic": "different", "_principal": "alice"},
            principal="alice",
            idempotency_key="request-1",
            request_hash="different-hash",
        )

    assert len(store.list_jobs()) == 1
    assert store.lookup_idempotency(principal="alice", idempotency_key="request-1") == ("job-idem-1", "hash-1")


def test_dashboard_job_event_history_tracks_lifecycle(tmp_path):
    db = tmp_path / "jobs.db"
    _create_schema(db)
    store = DashboardStore(db)
    store.ensure_indexes()
    store.insert_job("job-events", "topic", {"topic": "topic"})
    assert store.claim_job("job-events") is True
    assert store.update_job("job-events", status="done", step="Complete") == 1

    events = store.events_since("job-events")
    assert [event["event"] for event in events][:1] == ["JOB_CREATED"]
    assert ("queued", "running") in {(event["from_status"], event["to_status"]) for event in events}
    assert ("running", "done") in {(event["from_status"], event["to_status"]) for event in events}


def test_stale_idempotency_key_is_self_healed(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()

    def seed(conn):
        conn.execute(
            "INSERT INTO idempotency_keys(principal, idem_key, request_hash, job_id) VALUES (?,?,?,?)",
            ("alice", "stale-key", "hash-stale", "missing-job"),
        )
    store.write(seed)

    assert store.lookup_idempotency(principal="alice", idempotency_key="stale-key") is None
    with store.connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM idempotency_keys WHERE principal=? AND idem_key=?",
            ("alice", "stale-key"),
        ).fetchone() is None


def test_idempotency_hash_migration_is_atomic(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()

    def seed(conn):
        conn.execute(
            "INSERT INTO idempotency_keys(principal, idem_key, request_hash, job_id) VALUES (?,?,?,?)",
            ("alice", "legacy-key", "legacy-hash", "job-1"),
        )
        conn.execute(
            "INSERT INTO jobs(id, topic, status, step, params, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("job-1", "topic", "done", "complete", "{}", "2026-10-01", "2026-10-01"),
        )
    store.write(seed)

    assert store.migrate_idempotency_hash(
        principal="alice",
        idempotency_key="legacy-key",
        expected_old_hash="legacy-hash",
        new_hash="new-hash",
    ) is True
    assert store.lookup_idempotency(principal="alice", idempotency_key="legacy-key") == ("job-1", "new-hash")


def test_idempotent_concurrent_requests_create_one_job(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()

    def create(index):
        return store.insert_job_idempotent(
            f"job-{index}",
            "same request",
            {"topic": "same", "_principal": "tester"},
            principal="tester",
            idempotency_key="same-key",
            request_hash="same-hash",
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(create, range(100)))

    assert {job_id for job_id, _created in results} == {"job-0"}
    assert sum(created for _job_id, created in results) == 1


def test_stale_attempt_cannot_update_new_attempt(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()
    store.insert_job("job-1", "topic", {"topic": "topic"}, principal="tester")
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
        error="worker crashed",
    )
    assert store.retry_job("job-1", max_attempts=2) == 1
    assert store.claim_job(
        "job-1",
        attempt_id="attempt-b",
        worker_id="worker-b",
        lease_token="lease-b",
        workspace_dir=str(tmp_path / "attempt-b"),
    )
    assert not store.update_job_if_owned(
        "job-1",
        "attempt-a",
        "lease-a",
        step="stale worker",
    )
    assert store.is_attempt_owner("job-1", "attempt-b", "lease-b")


def test_explicit_schema_migration_upgrades_legacy_jobs(tmp_path):
    import sqlite3
    from dashboard_store import DashboardStore
    from ai_video_factory.db_migrations import CURRENT_SCHEMA_VERSION

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, topic TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'queued', step TEXT NOT NULL DEFAULT 'waiting', "
        "params TEXT NOT NULL DEFAULT '{}', pkg_dir TEXT, error TEXT, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    conn.close()

    store = DashboardStore(db)
    store.ensure_indexes()

    with store.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]

    assert {"principal", "attempt_id", "worker_token", "resource_units", "error_code"} <= columns
    assert version == CURRENT_SCHEMA_VERSION
