from __future__ import annotations

import sqlite3

import pytest

from dashboard_store import DashboardStore


def test_production_event_recording_does_not_create_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIVF_ENV", "production")
    monkeypatch.delenv("AIVF_ALLOW_RUNTIME_MIGRATIONS", raising=False)
    conn = sqlite3.connect(":memory:")

    with pytest.raises(sqlite3.OperationalError, match="no such table: job_events"):
        DashboardStore._record_event(conn, "job-1", "job_queued")

    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='job_events'"
    ).fetchone()
    assert table is None
    conn.close()


def test_explicit_runtime_migration_override_keeps_legacy_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AIVF_ENV", "production")
    monkeypatch.setenv("AIVF_ALLOW_RUNTIME_MIGRATIONS", "1")
    conn = sqlite3.connect(":memory:")

    DashboardStore._record_event(conn, "job-1", "job_queued")

    row = conn.execute("SELECT job_id, event FROM job_events").fetchone()
    assert row == ("job-1", "job_queued")
    conn.close()
