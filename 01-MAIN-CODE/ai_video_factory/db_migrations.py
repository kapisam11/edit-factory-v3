"""Explicit SQLite schema migrations for the dashboard store."""
from __future__ import annotations

import sqlite3

CURRENT_SCHEMA_VERSION = 6


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row["name"]) for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}


def migrate(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    applied = {
        int(row["version"])
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }

    # Version 1: lifecycle/recovery columns.
    if 1 not in applied:
        columns = _table_columns(conn)
        additions = {
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "worker_heartbeat_at": "TEXT",
            "principal": "TEXT NOT NULL DEFAULT 'unknown'",
            "attempt_id": "TEXT",
            "worker_token": "TEXT",
            "workspace_dir": "TEXT",
            "resource_units": "INTEGER NOT NULL DEFAULT 1",
            "reserved_disk_bytes": "INTEGER NOT NULL DEFAULT 0",
            "reserved_memory_bytes": "INTEGER NOT NULL DEFAULT 0",
            "error_code": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
        if "worker_heartbeat_at" in additions:
            conn.execute(
                "UPDATE jobs SET worker_heartbeat_at=updated_at "
                "WHERE worker_heartbeat_at IS NULL"
            )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (1)")

    # Version 2: durable job events.
    if 2 not in applied:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                event TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_events_job_id_id "
            "ON job_events(job_id, id)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (2)")

    # Version 3: fenced attempts.
    if 3 not in applied:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_attempts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                worker_id TEXT NOT NULL,
                lease_token TEXT NOT NULL,
                workspace_dir TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                heartbeat_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                error_code TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_attempts_job_status "
            "ON job_attempts(job_id, status, attempt_number)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (3)")

    # Version 4: administrative audit events.
    if 4 not in applied:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                principal TEXT NOT NULL,
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                resource_id TEXT,
                result TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_events_principal_created "
            "ON audit_events(principal, created_at)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (4)")

    # Version 5: idempotency/index hardening.
    if 5 not in applied:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                principal TEXT NOT NULL,
                idem_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                job_id TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT (unixepoch()),
                PRIMARY KEY (principal, idem_key)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_idempotency_created "
            "ON idempotency_keys(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_principal_status "
            "ON jobs(principal, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_attempt "
            "ON jobs(attempt_id)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (5)")

    # Version 6: performance indexes used by lifecycle operations.
    if 6 not in applied:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_created "
            "ON jobs(status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_updated "
            "ON jobs(status, updated_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_worker_heartbeat "
            "ON jobs(status, worker_heartbeat_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_logs_job_id_id "
            "ON job_logs(job_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_limits_client_ip_ts "
            "ON rate_limits(client_ip, ts)"
        )
        conn.execute("INSERT INTO schema_migrations(version) VALUES (6)")

    conn.commit()
    return CURRENT_SCHEMA_VERSION


__all__ = ["CURRENT_SCHEMA_VERSION", "migrate"]
