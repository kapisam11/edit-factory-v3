"""Reusable SQLite storage boundary for the production dashboard.

The web layer can keep its historical helper functions while routing connections
through this class. This gives the database layer one place for WAL, busy
timeouts, bounded write retries, and performance indexes.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional


class DashboardStore:
    """Small, dependency-free SQLite storage service used by the dashboard."""

    WRITE_RETRIES = 3
    RETRY_DELAY_SECONDS = 0.05
    MAX_LIST_LIMIT = 500

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path))

    @staticmethod
    def _is_busy(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return (
            "database is locked" in message
            or "database is busy" in message
            or "database table is locked" in message
        )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def write(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        for attempt in range(self.WRITE_RETRIES + 1):
            try:
                with self.connect() as conn:
                    return operation(conn)
            except sqlite3.OperationalError as exc:
                if attempt >= self.WRITE_RETRIES or not self._is_busy(exc):
                    raise
                time.sleep(self.RETRY_DELAY_SECONDS * (2**attempt))
        raise AssertionError("unreachable")

    def ensure_indexes(self) -> None:
        """Add indexes that match the dashboard's queue/history query patterns."""
        with self.connect() as conn:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_created "
                "ON jobs(status, created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_updated "
                "ON jobs(status, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_logs_job_id_id "
                "ON job_logs(job_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rate_limits_client_ip_ts "
                "ON rate_limits(client_ip, ts)"
            )

    def insert_job(self, job_id: str, topic: str, params: dict) -> None:
        self.write(
            lambda conn: conn.execute(
                "INSERT INTO jobs (id, topic, status, step, params) VALUES (?, ?, 'queued', 'waiting', ?)",
                (job_id, topic, json.dumps(params)),
            )
        )

    def update_job(self, job_id: str, **kwargs: Any) -> int:
        allowed = {"status", "step", "params", "pkg_dir", "error"}
        invalid = set(kwargs) - allowed
        if invalid:
            raise ValueError(f"Invalid job fields: {sorted(invalid)}")
        if not kwargs:
            return 0
        fields = ", ".join(f"{key}=?" for key in kwargs)
        return int(
            self.write(
                lambda conn: conn.execute(
                    f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    list(kwargs.values()) + [job_id],
                ).rowcount
            )
        )

    def update_job_if_status(
        self,
        job_id: str,
        expected_statuses: tuple[str, ...],
        **kwargs: Any,
    ) -> int:
        allowed = {"status", "step", "params", "pkg_dir", "error"}
        invalid = set(kwargs) - allowed
        if invalid or not kwargs or not expected_statuses:
            raise ValueError("Invalid conditional job update")
        fields = ", ".join(f"{key}=?" for key in kwargs)
        placeholders = ",".join("?" for _ in expected_statuses)
        values = list(kwargs.values()) + [job_id, *expected_statuses]
        return int(
            self.write(
                lambda conn: conn.execute(
                    f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP "
                    f"WHERE id=? AND status IN ({placeholders})",
                    values,
                ).rowcount
            )
        )

    def get_job(self, job_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, limit: int = 100) -> list[dict]:
        try:
            bounded_limit = max(1, min(int(limit), self.MAX_LIST_LIMIT))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer") from exc
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (bounded_limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def append_log(self, job_id: str, level: str, message: str) -> None:
        self.write(
            lambda conn: conn.execute(
                "INSERT INTO job_logs (job_id, level, message) VALUES (?, ?, ?)",
                (job_id, level.upper(), str(message)[:10000]),
            )
        )

    def logs_since(self, job_id: str, last_id: int = 0) -> list[dict]:
        try:
            bounded_last_id = max(0, int(last_id))
        except (TypeError, ValueError) as exc:
            raise ValueError("last_id must be an integer") from exc
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, level, message FROM job_logs "
                "WHERE job_id=? AND id>? ORDER BY id ASC",
                (job_id, bounded_last_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def check_rate_limit(self, client_ip: str, max_requests: int = 10, window_seconds: int = 60) -> bool:
        if max_requests < 1 or window_seconds < 1:
            raise ValueError("rate-limit values must be positive")
        now = time.time()
        cutoff = now - window_seconds

        def write(conn: sqlite3.Connection) -> bool:
            conn.execute("DELETE FROM rate_limits WHERE ts<?", (cutoff,))
            count = conn.execute(
                "SELECT COUNT(*) FROM rate_limits WHERE client_ip=?", (client_ip,)
            ).fetchone()[0]
            if count >= max_requests:
                return False
            conn.execute("INSERT INTO rate_limits (client_ip,ts) VALUES (?,?)", (client_ip, now))
            return True

        return self.write(write)
