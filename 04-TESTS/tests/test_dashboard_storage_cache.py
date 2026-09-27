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
    store.insert_job("job-1", "topic", {})
    assert store.claim_job("job-1") is True
    store.update_job("job-1", status="error", error="provider returned 429")
    assert store.retry_job("job-1", max_attempts=1) == 1
    assert store.get_job("job-1")["retry_count"] == 1
    store.update_job("job-1", status="error", error="invalid configuration")
    with pytest.raises(JobRetryNotAllowed, match="deterministic|maximum"):
        store.retry_job("job-1", max_attempts=1)
