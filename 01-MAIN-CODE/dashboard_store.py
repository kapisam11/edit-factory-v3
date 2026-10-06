"""Reusable SQLite storage boundary for the production dashboard.

The web layer can keep its historical helper functions while routing connections
through this class. This gives the database layer one place for WAL, busy
timeouts, bounded write retries, and performance indexes.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from ai_video_factory.job_state import validate_transition
from ai_video_factory.retry_policy import backoff_seconds, is_retryable_error
from ai_video_factory.db_migrations import migrate_database, verify_database_schema


class JobAdmissionError(RuntimeError):
    """Raised when an atomic dashboard admission limit rejects a new job."""


class IdempotencyConflict(JobAdmissionError):
    """Raised when a request key is reused for a different request payload."""


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
                time.sleep(backoff_seconds(attempt + 1, base=self.RETRY_DELAY_SECONDS, cap=1.0))
        raise AssertionError("unreachable")

    @staticmethod
    def _ensure_job_columns(conn: sqlite3.Connection) -> None:
        """Apply lightweight job-table migrations required by lifecycle features."""
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if not columns:
            return
        if "error_code" not in columns:
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN error_code TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        if "retry_count" not in columns:
            try:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        if "principal" not in columns:
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN principal TEXT NOT NULL DEFAULT 'unknown'")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        if "current_attempt" not in columns:
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN current_attempt INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        if "worker_id" not in columns:
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN worker_id TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        if "lease_token" not in columns:
            try:
                conn.execute("ALTER TABLE jobs ADD COLUMN lease_token TEXT")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        if "worker_heartbeat_at" not in columns:
            try:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN worker_heartbeat_at TEXT"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
            try:
                conn.execute(
                    "UPDATE jobs SET worker_heartbeat_at=updated_at "
                    "WHERE worker_heartbeat_at IS NULL"
                )
            except sqlite3.OperationalError as exc:
                if "no such column" not in str(exc).lower():
                    raise

    def ensure_indexes(self) -> None:
        """Ensure the storage boundary is ready without mutating production schema."""
        environment = os.environ.get("AIVF_ENV", "development").strip().lower()
        allow_runtime = os.environ.get("AIVF_ALLOW_RUNTIME_MIGRATIONS", "0").strip() == "1"
        if environment == "production" and not allow_runtime:
            verify_database_schema(self.db_path)
            return
        migrate_database(self.db_path)
        with self.connect() as conn:
            # DashboardStore is also used against fresh/empty SQLite files in
            # tests and recovery paths. Bootstrap the minimal storage schema
            # before creating triggers or indexes that reference these tables.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    step TEXT NOT NULL DEFAULT 'waiting',
                    params TEXT NOT NULL DEFAULT '{}',
                    pkg_dir TEXT,
                    error TEXT,
                    error_code TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    worker_heartbeat_at TEXT,
                    current_attempt INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT,
                    lease_token TEXT,
                    principal TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    client_ip TEXT NOT NULL,
                    ts REAL NOT NULL
                )
            """)
            self._ensure_job_columns(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_principal_status ON jobs(principal, status)")
            conn.execute("""

                CREATE TRIGGER IF NOT EXISTS validate_job_status_transition_store
                BEFORE UPDATE OF status ON jobs
                WHEN NOT (
                    NEW.status = OLD.status OR
                    (OLD.status = 'queued' AND NEW.status IN ('running','cancelling','cancelled','error','interrupted')) OR
                    (OLD.status = 'running' AND NEW.status IN ('cancelling','cancelled','done','error','interrupted')) OR
                    (OLD.status = 'cancelling' AND NEW.status IN ('cancelled','error','interrupted')) OR
                    (OLD.status IN ('done','cancelled')) OR
                    (OLD.status IN ('error','interrupted') AND NEW.status = 'queued')
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid job status transition');
                END
            """)
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
                "CREATE INDEX IF NOT EXISTS idx_job_events_job_id_id ON job_events(job_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rate_limits_client_ip_ts "
                "ON rate_limits(client_ip, ts)"
            )
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

    @staticmethod
    def _record_event(
        conn: sqlite3.Connection,
        job_id: str,
        event: str,

        *,
        from_status: str | None = None,
        to_status: str | None = None,
        details: str = "",
    ) -> None:
        # Unit/integration callers can construct the legacy jobs schema without
        # running ensure_indexes(). Make event persistence self-contained.
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
            "INSERT INTO job_events(job_id, from_status, to_status, event, details) VALUES (?,?,?,?,?)",
            (job_id, from_status, to_status, str(event)[:120], str(details)[:4000]),
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
        principal_value = str(principal).strip() or "unknown"

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
                    "SELECT principal, params FROM jobs WHERE status IN ('queued','running')"
                ).fetchall()
                count = 0
                for row in rows:
                    try:
                        payload = json.loads(row["params"] or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    row_principal = str(row["principal"] or "").strip()
                    if row_principal == str(principal).strip() or str(payload.get("_principal", "")) == str(principal).strip():
                        count += 1
                if count >= int(principal_limit):
                    raise JobAdmissionError("principal queue capacity reached")
            conn.execute(
                "INSERT INTO jobs (id, topic, status, step, params, principal, created_at, updated_at) "
                "VALUES (?, ?, 'queued', 'waiting', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (job_id, topic, encoded, principal_value),
            )
            self._record_event(conn, job_id, "created", to_status="queued", details=f"topic={topic[:200]}")

        self.write(write)

    def lookup_idempotency(
        self,
        *,
        principal: str,
        idempotency_key: str,
    ) -> tuple[str, str] | None:
        """Return (job_id, request_hash) for an existing request key, if present."""
        key = str(idempotency_key).strip()
        principal_value = str(principal).strip() or "unknown"
        if not key:
            return None
        with self.connect() as conn:
            row = conn.execute(
                "SELECT job_id, request_hash FROM idempotency_keys WHERE principal=? AND idem_key=?",
                (principal_value, key),
            ).fetchone()
            if row is None:
                return None
            job_exists = conn.execute(
                "SELECT 1 FROM jobs WHERE id=?",
                (str(row["job_id"]),),
            ).fetchone()
            if job_exists is None:
                conn.execute(
                    "DELETE FROM idempotency_keys WHERE principal=? AND idem_key=?",
                    (principal_value, key),
                )
                return None
        return str(row["job_id"]), str(row["request_hash"])


    def migrate_idempotency_hash(
        self,
        *,
        principal: str,
        idempotency_key: str,
        expected_old_hash: str,
        new_hash: str,
    ) -> bool:
        """Atomically upgrade a legacy idempotency fingerprint after safe replay validation."""
        key = str(idempotency_key).strip()
        principal_value = str(principal).strip() or "unknown"
        old_hash = str(expected_old_hash).strip()
        replacement = str(new_hash).strip()
        if not key or not old_hash or not replacement:
            return False

        def write(conn: sqlite3.Connection) -> bool:
            changed = conn.execute(
                "UPDATE idempotency_keys SET request_hash=? "
                "WHERE principal=? AND idem_key=? AND request_hash=?",
                (replacement, principal_value, key, old_hash),
            ).rowcount
            return bool(changed)

        return bool(self.write(write))


    def insert_job_idempotent(
        self,
        job_id: str,
        topic: str,
        params: dict,
        *,
        principal: str,
        idempotency_key: str,
        request_hash: str,
        max_queued_jobs: int | None = None,
        principal_limit: int | None = None,
    ) -> tuple[str, bool]:
        """Atomically reserve an idempotency key and insert the job.

        Returns (job_id, created). A repeated identical request returns the
        original job without creating a second worker. Reusing a key for a
        different request is rejected as a conflict.
        """
        key = str(idempotency_key).strip()
        if not 1 <= len(key) <= 200:
            raise ValueError("idempotency key must be 1..200 characters")
        principal_value = str(principal).strip() or "unknown"
        fingerprint = str(request_hash).strip()
        if not fingerprint:
            raise ValueError("request hash is required")
        encoded = json.dumps(params)

        def write(conn: sqlite3.Connection) -> tuple[str, bool]:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT job_id, request_hash FROM idempotency_keys WHERE principal=? AND idem_key=?",
                (principal_value, key),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != fingerprint:
                    raise IdempotencyConflict("idempotency key was already used for a different request")
                return str(existing["job_id"]), False
            if max_queued_jobs is not None:
                queued = int(conn.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0])
                if queued >= int(max_queued_jobs):
                    raise JobAdmissionError("queue capacity reached")
            if principal_limit is not None:
                count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM jobs WHERE principal=? AND status IN ('queued','running')",
                        (principal_value,),
                    ).fetchone()[0]
                )
                if count >= int(principal_limit):
                    raise JobAdmissionError("principal queue capacity reached")
            conn.execute(
                "INSERT INTO idempotency_keys(principal, idem_key, request_hash, job_id) VALUES (?,?,?,?)",
                (principal_value, key, fingerprint, job_id),
            )
            conn.execute(
                "INSERT INTO jobs (id, topic, status, step, params, principal, created_at, updated_at) "
                "VALUES (?, ?, 'queued', 'waiting', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (job_id, topic, encoded, principal_value),
            )
            self._record_event(conn, job_id, "created", to_status="queued", details="idempotent request")
            return job_id, True

        return self.write(write)

    def prune_idempotency(self, *, max_age_seconds: int = 86400, now: float | None = None) -> int:
        """Bound idempotency storage so request keys cannot become unbounded state."""
        cutoff = (time.time() if now is None else float(now)) - max(60, int(max_age_seconds))
        return int(self.write(lambda conn: conn.execute(
            "DELETE FROM idempotency_keys WHERE created_at<?", (cutoff,)
        ).rowcount))

    def update_job(self, job_id: str, **kwargs: Any) -> int:
        allowed = {"status", "step", "params", "pkg_dir", "error", "error_code"}
        invalid = set(kwargs) - allowed
        if invalid:
            raise ValueError(f"Invalid job fields: {sorted(invalid)}")
        if not kwargs:
            return 0

        def write(conn: sqlite3.Connection) -> int:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return 0
            previous_status = str(row["status"])
            target_status = str(kwargs.get("status", previous_status))
            if "status" in kwargs:
                validate_transition(previous_status, target_status)
            fields = ", ".join(f"{key}=?" for key in kwargs)
            changed = int(conn.execute(
                f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                list(kwargs.values()) + [job_id],
            ).rowcount)
            if changed and target_status != previous_status:
                self._record_event(
                    conn,
                    job_id,
                    "status_change",
                    from_status=previous_status,
                    to_status=target_status,
                    details=str(kwargs.get("error") or kwargs.get("step") or ""),
                )
            return changed

        return int(self.write(write))

    def update_job_compat(self, job_id: str, **kwargs: Any) -> int:
        """Preserve the legacy direct-completion helper without weakening lifecycle rules."""
        if kwargs.get("status") != "done":
            return self.update_job(job_id, **kwargs)

        def write(conn: sqlite3.Connection) -> int:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return 0
            current = str(row["status"])
            if current == "queued":
                changed = conn.execute(
                    "UPDATE jobs SET status='running', step='starting', updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status='queued'",
                    (job_id,),
                ).rowcount
                if changed != 1:
                    return 0
                current = "running"
            validate_transition(current, "done")
            fields = ", ".join(f"{key}=?" for key in kwargs)
            return int(conn.execute(
                f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                list(kwargs.values()) + [job_id],
            ).rowcount)

        return int(self.write(write))
    def reserve_resources(
        self,
        job_id: str,
        *,
        input_bytes: int,
        reserved_bytes: int,
        cpu_weight: float,
        memory_bytes: int,
        max_reserved_disk_bytes: int,
        max_cpu_weight: float,
        max_memory_bytes: int,
    ) -> bool:
        """Atomically reserve finite host capacity for a queued/running job."""
        values = (
            max(0, int(input_bytes)),
            max(0, int(reserved_bytes)),
            max(0.01, float(cpu_weight)),
            max(0, int(memory_bytes)),
        )

        def write(conn: sqlite3.Connection) -> bool:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT 1 FROM resource_reservations WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if existing is not None:
                return True
            disk = int(conn.execute("SELECT COALESCE(SUM(reserved_bytes),0) FROM resource_reservations").fetchone()[0])
            cpu = float(conn.execute("SELECT COALESCE(SUM(cpu_weight),0) FROM resource_reservations").fetchone()[0])
            memory = int(conn.execute("SELECT COALESCE(SUM(memory_bytes),0) FROM resource_reservations").fetchone()[0])
            if (
                disk + values[1] > int(max_reserved_disk_bytes)
                or cpu + values[2] > float(max_cpu_weight)
                or memory + values[3] > int(max_memory_bytes)
            ):
                return False
            conn.execute(
                """
                INSERT INTO resource_reservations(
                    job_id,input_bytes,reserved_bytes,cpu_weight,memory_bytes,created_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (*values, time.time()),
            )
            self._record_event(
                conn,
                job_id,
                "resource_reserved",
                details=(
                    f"disk={values[1]};cpu={values[2]:.3f};"
                    f"memory={values[3]}"
                ),
            )
            return True

        return bool(self.write(write))

    def release_resources(self, job_id: str) -> bool:
        """Release a job's reserved host capacity."""
        def write(conn: sqlite3.Connection) -> bool:
            changed = int(
                conn.execute(
                    "DELETE FROM resource_reservations WHERE job_id=?",
                    (job_id,),
                ).rowcount
            )
            if changed:
                self._record_event(conn, job_id, "resource_released")
            return changed == 1

        return bool(self.write(write))

    def begin_attempt(self, job_id: str, worker_id: str, workspace: str) -> dict[str, str] | None:
        """Create a fenced execution attempt and bind its lease to the job."""
        attempt_id = uuid.uuid4().hex
        lease_token = uuid.uuid4().hex
        worker_value = str(worker_id).strip() or "unknown"
        workspace_value = str(workspace).strip()
        if not workspace_value:
            raise ValueError("attempt workspace is required")

        def write(conn: sqlite3.Connection) -> dict[str, str] | None:
            row = conn.execute(
                "SELECT status, current_attempt FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row is None or str(row["status"]) not in {"running", "cancelling"}:
                return None
            attempt_number = int(row["current_attempt"] or 0) + 1
        attempt_workspace = str(Path(workspace) / attempt_id)
            conn.execute(
                """
                INSERT INTO job_attempts(
                    id, job_id, attempt_number, worker_id, lease_token,
                    workspace, heartbeat_at, status
                ) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP,'running')
                """,
                (
                    attempt_id,
                    job_id,
                    attempt_number,
                    worker_value,
                    lease_token,
                    attempt_workspace,
                ),
            )
            conn.execute(
                """
                UPDATE jobs
                SET current_attempt=?, worker_id=?, lease_token=?,
                    worker_heartbeat_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status IN ('running','cancelling')
                """,
                (attempt_number, worker_value, lease_token, job_id),
            )
            self._record_event(
                conn,
                job_id,
                "attempt_started",
                details=f"attempt_id={attempt_id};attempt_number={attempt_number}",
            )
            return {
                "attempt_id": attempt_id,
                "lease_token": lease_token,
                "worker_id": worker_value,
                "attempt_number": str(attempt_number),
            }

        return self.write(write)

    def heartbeat_attempt(self, job_id: str, attempt_id: str, lease_token: str) -> bool:
        """Refresh the attempt lease only when the worker still owns the fence."""
        def write(conn: sqlite3.Connection) -> int:
            changed = int(
                conn.execute(
                    """
                    UPDATE jobs
                    SET worker_heartbeat_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=? AND status IN ('running','cancelling') AND lease_token=?
                    """,
                    (job_id, lease_token),
                ).rowcount
            )
            if changed != 1:
                return 0
            return int(
                conn.execute(
                    """
                    UPDATE job_attempts
                    SET heartbeat_at=CURRENT_TIMESTAMP
                    WHERE id=? AND job_id=? AND lease_token=? AND status='running'
                    """,
                    (attempt_id, job_id, lease_token),
                ).rowcount
            )

        return bool(self.write(write))

    def attempt_can_publish(self, job_id: str, attempt_id: str, lease_token: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM jobs j
                JOIN job_attempts a ON a.job_id=j.id
                WHERE j.id=? AND j.status IN ('running','cancelling')
                  AND j.lease_token=? AND a.id=? AND a.lease_token=?
                  AND a.status='running'
                """,
                (job_id, lease_token, attempt_id, lease_token),
            ).fetchone()
        return row is not None

    def finish_attempt(
        self,
        job_id: str,
        attempt_id: str,
        lease_token: str,
        *,
        status: str,
        error_code: str | None = None,
    ) -> bool:
        """Close an attempt only when the lease still belongs to that worker."""
        def write(conn: sqlite3.Connection) -> int:
            changed = int(
                conn.execute(
                    """
                    UPDATE job_attempts
                    SET status=?, error_code=?, finished_at=CURRENT_TIMESTAMP
                    WHERE id=? AND job_id=? AND lease_token=? AND status='running'
                    """,
                    (
                        str(status),
                        None if error_code is None else str(error_code),
                        attempt_id,
                        job_id,
                        lease_token,
                    ),
                ).rowcount
            )
            if changed != 1:
                return 0
            conn.execute(
                """
                UPDATE jobs
                SET worker_id=NULL, lease_token=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND lease_token=?
                """,
                (job_id, lease_token),
            )
            self._record_event(
                conn,
                job_id,
                "attempt_finished",
                details=f"attempt_id={attempt_id};status={status}",
            )
            return changed

        return bool(self.write(write))

    def claim_job(self, job_id: str) -> bool:
        """Atomically claim a queued job and establish its heartbeat lease."""
        def write(conn: sqlite3.Connection) -> int:
            self._ensure_job_columns(conn)
            row = conn.execute(
                "SELECT status FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row is None or str(row["status"]) != "queued":
                return 0
            validate_transition("queued", "running")
            changed = int(
                conn.execute(
                    "UPDATE jobs SET status='running', step='starting', "
                    "worker_heartbeat_at=CURRENT_TIMESTAMP, error=NULL, "
                    "updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status='queued'",
                    (job_id,),
                ).rowcount
            )
            if changed:
                self._record_event(
                    conn,
                    job_id,
                    "status_change",
                    from_status="queued",
                    to_status="running",
                    details="worker lease claimed",
                )
            return changed
        return int(self.write(write)) == 1

    def heartbeat_job(self, job_id: str) -> bool:
        """Refresh the durable worker lease for a non-terminal job."""
        def write(conn: sqlite3.Connection) -> int:
            self._ensure_job_columns(conn)
            return int(
                conn.execute(
                    "UPDATE jobs SET worker_heartbeat_at=CURRENT_TIMESTAMP, "
                    "updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status IN ('running','cancelling')",
                    (job_id,),
                ).rowcount
            )
        return bool(self.write(write))

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
            changed = int(conn.execute(
                "UPDATE jobs SET status='queued', step='waiting', error=NULL, pkg_dir=NULL, "
                "retry_count=retry_count+1, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status IN ('error','interrupted') AND retry_count<?",
                (job_id, max_attempts),
            ).rowcount)
            if changed:
                self._record_event(
                    conn,
                    job_id,
                    "retry",
                    from_status=status,
                    to_status="queued",
                    details=f"attempt={attempts + 1}",
                )
            return changed

        return int(self.write(write))

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

    def record_audit_event(
        self,
        *,
        principal: str,
        action: str,
        resource: str,
        resource_id: str | None = None,
        remote_addr: str | None = None,
        user_agent: str | None = None,
        result: str = "success",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist a security/audit action separately from job lifecycle events."""
        payload = json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False)

        def write(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO audit_events(
                    principal, action, resource, resource_id, remote_addr,
                    user_agent, result, metadata
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    str(principal).strip() or "unknown",
                    str(action).strip() or "unknown",
                    str(resource).strip() or "unknown",
                    None if resource_id is None else str(resource_id),
                    None if remote_addr is None else str(remote_addr),
                    None if user_agent is None else str(user_agent)[:512],
                    str(result).strip() or "unknown",
                    payload,
                ),
            )

        self.write(write)

    def audit_events_since(self, *, principal: str | None = None, last_id: int = 0, limit: int = 200) -> list[dict]:
        bounded_limit = max(1, min(int(limit), 1000))
        with self.connect() as conn:
            if principal:
                rows = conn.execute(
                    "SELECT * FROM audit_events WHERE id>? AND principal=? ORDER BY id ASC LIMIT ?",
                    (max(0, int(last_id)), str(principal), bounded_limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_events WHERE id>? ORDER BY id ASC LIMIT ?",
                    (max(0, int(last_id)), bounded_limit),
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

    def events_since(self, job_id: str, last_id: int = 0) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, from_status, to_status, event, details "
                "FROM job_events WHERE job_id=? AND id>? ORDER BY id ASC",
                (job_id, max(0, int(last_id))),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_events(self, *, max_age_seconds: int = 30 * 86400, now: float | None = None) -> int:
        cutoff = (time.time() if now is None else float(now)) - max(300, int(max_age_seconds))
        return int(self.write(lambda conn: conn.execute(
            "DELETE FROM job_events WHERE strftime('%s', created_at)<?", (str(int(cutoff)),)
        ).rowcount))

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
