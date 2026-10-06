"""Explicit SQLite schema versioning for the dashboard store.

The migration layer is deliberately small and additive. Existing installations
remain readable while every schema upgrade is recorded transactionally.
"""
from __future__ import annotations

from sqlite3 import Connection

CURRENT_SCHEMA_VERSION = 2


def ensure_schema_migrations_table(conn: Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            description TEXT NOT NULL
        )
    """)


def apply_schema_migrations(conn: Connection) -> int:
    ensure_schema_migrations_table(conn)
    current = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    current = int(current or 0)

    if current < 1:
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, description) "
            "VALUES (1, 'dashboard lifecycle baseline')"
        )
        current = 1

    if current < 2:
        # The column-level migration is handled by DashboardStore._ensure_job_columns
        # so legacy databases receive exactly the same additive upgrades as fresh ones.
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, description) "
            "VALUES (2, 'attempt fencing, principals, resource reservations and error codes')"
        )
        current = 2

    if current > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {current} is newer than supported version {CURRENT_SCHEMA_VERSION}"
        )
    return current


__all__ = ["CURRENT_SCHEMA_VERSION", "ensure_schema_migrations_table", "apply_schema_migrations"]
