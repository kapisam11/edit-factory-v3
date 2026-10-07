"""Explicit SQLite schema migrations for production deployments."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterable

CURRENT_SCHEMA_VERSION = 5

_REQUIRED_COLUMNS = {
    "jobs": {
        "id", "topic", "status", "step", "params", "principal", "pkg_dir",
        "error", "error_code", "retry_count", "worker_heartbeat_at", "current_attempt",
        "worker_id", "lease_token", "created_at", "updated_at",
    },
    "job_logs": {"id", "job_id", "created_at", "level", "message"},
    "job_events": {"id", "job_id", "from_status", "to_status", "event", "details", "created_at"},
    "job_attempts": {
        "id", "job_id", "attempt_number", "worker_id", "lease_token",
        "workspace", "started_at", "heartbeat_at", "finished_at", "status", "error_code",
    },
    "job_artifacts": {
        "id", "job_id", "attempt_id", "kind", "path", "sha256", "size_bytes", "created_at",
    },
    "audit_events": {
        "id", "created_at", "principal", "action", "resource", "resource_id",
        "remote_addr", "user_agent", "result", "metadata",
    },
    "resource_reservations": {
        "job_id", "input_bytes", "reserved_bytes", "cpu_weight", "memory_bytes", "created_at",
    },
    "idempotency_keys": {"principal", "idem_key", "request_hash", "job_id", "created_at"},
    "settings": {"key", "value"},
    "rate_limits": {"client_ip", "ts"},
}


class SchemaMismatch(RuntimeError):
    """Raised when production starts with a schema that was not migrated."""


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, ddl: str
) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def migrate_database(path: str | Path) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _connect(target) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        current = int(row["version"]) if row else 0
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                step TEXT NOT NULL DEFAULT 'waiting',
                params TEXT NOT NULL DEFAULT '{}',
                principal TEXT NOT NULL DEFAULT 'unknown',
                pkg_dir TEXT,
                error TEXT,
                error_code TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                worker_heartbeat_at TEXT,
                current_attempt INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT,
                lease_token TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _add_column_if_missing(conn, "jobs", "principal", "TEXT NOT NULL DEFAULT 'unknown'")
        _add_column_if_missing(conn, "jobs", "error_code", "TEXT")
        _add_column_if_missing(conn, "jobs", "retry_count", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "jobs", "worker_heartbeat_at", "TEXT")
        _add_column_if_missing(conn, "jobs", "current_attempt", "INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "jobs", "worker_id", "TEXT")
        _add_column_if_missing(conn, "jobs", "lease_token", "TEXT")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT,
                event TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_attempts (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                worker_id TEXT,
                lease_token TEXT NOT NULL,
                workspace TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                heartbeat_at TEXT,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                error_code TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                attempt_id TEXT,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT,
                size_bytes INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                principal TEXT NOT NULL,
                action TEXT NOT NULL,
                resource TEXT NOT NULL,
                resource_id TEXT,
                remote_addr TEXT,
                user_agent TEXT,
                result TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_reservations (
                job_id TEXT PRIMARY KEY,
                input_bytes INTEGER NOT NULL DEFAULT 0,
                reserved_bytes INTEGER NOT NULL DEFAULT 0,
                cpu_weight REAL NOT NULL DEFAULT 1.0,
                memory_bytes INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                principal TEXT NOT NULL,
                idem_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                job_id TEXT NOT NULL,
                created_at REAL NOT NULL DEFAULT (unixepoch()),
                PRIMARY KEY (principal, idem_key)
            )
            """
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS rate_limits (client_ip TEXT NOT NULL, ts REAL NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_principal_status ON jobs(principal, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_logs_job_id_id ON job_logs(job_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_events_job_id_id ON job_events(job_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_attempts_job_id_id ON job_attempts(job_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_job_artifacts_job_id ON job_artifacts(job_id, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_events_principal_created "
            "ON audit_events(principal, created_at)"
        )

        conn.execute(
            """
            UPDATE jobs
            SET principal=COALESCE(
                NULLIF(
                    CASE
                        WHEN principal IS NULL OR principal='' OR principal='unknown'
                        THEN json_extract(params, '$._principal')
                        ELSE principal
                    END,
                    ''
                ),
                'unknown'
            )
            WHERE principal IS NULL OR principal='' OR principal='unknown'
            """
        )

        if current == 0:
            current = 1
        if current < 2:
            current = 2
        if current < 3:
            current = 3
        if current < 4:
            current = 4
        if current < 5:
            current = 5
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version(version) VALUES (?)", (CURRENT_SCHEMA_VERSION,))
        return CURRENT_SCHEMA_VERSION


def verify_database_schema(path: str | Path) -> None:
    target = Path(path)
    if not target.exists():
        raise SchemaMismatch(f"database does not exist: {target}")
    with _connect(target) as conn:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None or int(row["version"]) != CURRENT_SCHEMA_VERSION:
            actual = int(row["version"]) if row else 0
            raise SchemaMismatch(
                f"database schema version {actual} != required {CURRENT_SCHEMA_VERSION}; "
                "run aivf-db-migrate before starting production"
            )
        for table, required in _REQUIRED_COLUMNS.items():
            actual_columns = _columns(conn, table)
            missing = sorted(required - actual_columns)
            if missing:
                raise SchemaMismatch(
                    f"database table {table} is missing columns: {', '.join(missing)}"
                )


def main(argv: Iterable[str] | None = None) -> int:
    import sys

    args = list(argv or sys.argv[1:])
    path = args[0] if args else os.environ.get("AIVF_DB_PATH", "state/jobs.db")
    version = migrate_database(path)
    print(f"Database migrated to schema version {version}: {Path(path).resolve()}")
    return 0


__all__ = ["CURRENT_SCHEMA_VERSION", "SchemaMismatch", "migrate_database", "verify_database_schema", "main"]
