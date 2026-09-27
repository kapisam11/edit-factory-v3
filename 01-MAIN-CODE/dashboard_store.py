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

from ai_video_factory.job_state import validate_transition
from ai_video_factory.retry_policy import is_retryable_error


class JobAdmissionError(RuntimeError):
    """Raised when an atomic dashboard admission limit rejects a new job."""


class JobRetryNotAllowed(RuntimeError):
    """Raised when a retry is exhausted or the recorded failure is deterministic."""



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

    def insert_job(
        self,
        job_id: str,
        topic: str,
        params: dict,
        *,
        max_queued_jobs: int | None = None,
        principal: str | None = None,
        principal_limit: int | None = None,
    ) -> None:
        """Insert a queued job while enforcing admission limits in one transaction."""
        encoded = json.dumps(params)

        def write(conn: sqlite3.Connection) -> None:
            # Serialize admission with all other writers so quota checks and the
            # subsequent INSERT cannot race across concurrent dashboard requests.
            conn.execute("BEGIN IMMEDIATE")
            if max_queued_jobs is not None:
                queued = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM jobs WHERE status='queued'"
                    ).fetchone()[0]
                )
                if queued >= int(max_queued_jobs):
                    raise JobAdmissionError("queue capacity reached")
            if principal is not None and principal_limit is not None:
                rows = conn.execute(
                    "SELECT params FROM jobs WHERE status IN ('queued','running')"
                ).fetchall()
                count = 0
                for row in rows:
                    try:
                        payload = json.loads(row["params"] or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if str(payload.get("_principal", "")) == principal:
                        count += 1
                if count >= int(principal_limit):
                    raise JobAdmissionError("principal queue capacity reached")
            conn.execute(
                "INSERT INTO jobs (id, topic, status, step, params, created_at, updated_at) "
                "VALUES (?, ?, 'queued', 'waiting', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (job_id, topic, encoded),
            )

        self.write(write)

    def update_job(self, job_id: str, **kwargs: Any) -> int:
        allowed = {"status", "step", "params", "pkg_dir", "error"}
        invalid = set(kwargs) - allowed
        if invalid:
            raise ValueError(f"Invalid job fields: {sorted(invalid)}")
        if not kwargs:
            return 0

        def write(conn: sqlite3.Connection) -> int:
            if "status" in kwargs:
                row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row is None:
                    return 0
                validate_transition(str(row["status"]), str(kwargs["status"]))
            fields = ", ".join(f"{key}=?" for key in kwargs)
            return int(conn.execute(
                f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                list(kwargs.values()) + [job_id],
            ).rowcount)

        return int(self.write(write))

    def claim_job(self, job_id: str) -> bool:
        """Atomically claim one queued job before starting a worker process."""
        changed = self.update_job_if_status(
            job_id,
            ("queued",),
            status="running",
            step="starting",
            error=None,
        )
        return changed == 1

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

        def write(conn: sqlite3.Connection) -> int:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or str(row["status"]) not in expected_statuses:
                return 0
            if "status" in kwargs:
                validate_transition(str(row["status"]), str(kwargs["status"]))
            fields = ", ".join(f"{key}=?" for key in kwargs)
            values = list(kwargs.values()) + [job_id, *expected_statuses]
            placeholders = ",".join("?" for _ in expected_statuses)
            return int(conn.execute(
                f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP "
                f"WHERE id=? AND status IN ({placeholders})",
                values,
            ).rowcount)

        return int(self.write(write))

    def retry_job(self, job_id: str, *, max_attempts: int = 3) -> int:
        """Atomically requeue one failed/interrupted job after retry-policy checks."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

        def write(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT status, error, retry_count FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                return 0
            status = str(row["status"])
            if status not in {"error", "interrupted"}:
                raise JobRetryNotAllowed("only failed or interrupted jobs can be retried")
            attempts = int(row["retry_count"] or 0)
            if attempts >= max_attempts:
                raise JobRetryNotAllowed("maximum retry attempts reached")
            if status == "error" and row["error"]:
                if not is_retryable_error(RuntimeError(str(row["error"]))):
                    raise JobRetryNotAllowed("recorded job failure is deterministic and should not be retried")
            return int(conn.execute(
                "UPDATE jobs SET status='queued', step='waiting', error=NULL, pkg_dir=NULL, "
                "retry_count=retry_count+1, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status IN ('error','interrupted') AND retry_count<?",
                (job_id, max_attempts),
            ).rowcount)
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
