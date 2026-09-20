import json
import sqlite3

import pytest

from resource_governor import (
    ResourceLimitExceeded,
    acquire_sse,
    check_job_creation_limits,
    directory_size,
    release_sse,
)


def test_principal_queue_limit(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE jobs(params TEXT, status TEXT)")
        for _ in range(2):
            conn.execute(
                "INSERT INTO jobs(params,status) VALUES(?, 'queued')",
                (json.dumps({"_principal": "same-client"}),),
            )
    monkeypatch.setattr("resource_governor.MAX_QUEUED_PER_PRINCIPAL", 2)
    with pytest.raises(ResourceLimitExceeded, match="principal queue capacity"):
        check_job_creation_limits(
            lambda: sqlite3.connect(db_path),
            "same-client",
            (tmp_path,),
        )


def test_storage_quota_rejects_before_creation(monkeypatch, tmp_path):
    marker = tmp_path / "large.bin"
    marker.write_bytes(b"x" * 32)
    db_path = tmp_path / "jobs.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE jobs(params TEXT, status TEXT)")
    monkeypatch.setattr("resource_governor.MAX_TOTAL_STORAGE_BYTES", 16)
    with pytest.raises(ResourceLimitExceeded, match="total storage quota"):
        check_job_creation_limits(lambda: sqlite3.connect(db_path), "client", (tmp_path,))


def test_sse_counter_is_bounded():
    import resource_governor as governor
    original = governor.MAX_SSE_CONNECTIONS
    governor.MAX_SSE_CONNECTIONS = 2
    try:
        assert acquire_sse() is True
        assert acquire_sse() is True
        assert acquire_sse() is False
        release_sse()
        assert acquire_sse() is True
    finally:
        release_sse()
        release_sse()
        governor.MAX_SSE_CONNECTIONS = original
