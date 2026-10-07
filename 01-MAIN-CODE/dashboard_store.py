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
from pathlib import Path
from typing import Any, Callable, Optional

from ai_video_factory.job_state import validate_transition
from ai_video_factory.job_events import EVENT_VOCABULARY
from ai_video_factory.db_migrations import migrate
from ai_video_factory.retry_policy import backoff_seconds, is_retryable_error


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

    def ensure_indexes(self) -> None:
        """Apply explicit migrations, then install lifecycle triggers/indexes."""
        with self.connect() as conn:
            migrate(conn)
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

    def verify_schema(self) -> int:
        """Verify production schema is current without mutating it."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()
            if row is None or row[0] is None:
                raise RuntimeError("database schema has not been migrated")
            version = int(row[0])
            from ai_video_factory.db_migrations import CURRENT_SCHEMA_VERSION
            if version != CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema version {version} does not match required "
                    f"{CURRENT_SCHEMA_VERSION}"
                )
            required = {
                "jobs": {"principal", "attempt_id", "worker_token", "current_attempt", "error_code", "resource_class"},
                "job_attempts": {"lease_token", "worker_id", "heartbeat_at"},
                "job_artifacts": {"path", "sha256", "size_bytes"},
                "job_events": {"id", "event", "details"},
                "audit_events": {"principal", "action", "resource", "ip_address", "user_agent"},
            }
            for table, columns in required.items():
                actual = {str(item["name"]) for item in conn.execute(f"PRAGMA table_info({table})")}
                if not columns <= actual:
                    missing = ", ".join(sorted(columns - actual))
                    raise RuntimeError(f"database table {table} is missing columns: {missing}")
        return version

    @staticmethod
    def _resource_class_capacity(class_name: str) -> int:
        caps = {
            "LIGHT": max(1, int(os.environ.get("AIVF_RESOURCE_CLASS_LIGHT_CAPACITY", "4"))),
            "STANDARD": max(1, int(os.environ.get("AIVF_RESOURCE_CLASS_STANDARD_CAPACITY", "2"))),
            "HEAVY": max(1, int(os.environ.get("AIVF_RESOURCE_CLASS_HEAVY_CAPACITY", "1"))),
        }
        try:
            return caps[class_name]
        except KeyError as exc:
            raise JobAdmissionError(f"unsupported resource class: {class_name}") from exc

    @staticmethod
    def _resource_class_active_count(conn: sqlite3.Connection, class_name: str) -> int:
        return int(conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE resource_class=? AND status IN ('queued','running')",
            (class_name,),
        ).fetchone()[0])

    @staticmethod
    def _record_event(
        conn: sqlite3.Connection,
        job_id: str,
        event: str,

        *,
        from_status: str | None = None,
        to_status: str | None = None,
        details: Any = "",
    ) -> None:
        event_name = str(event).strip().upper()
        if event_name not in EVENT_VOCABULARY:
            raise ValueError(f"unsupported job event: {event_name}")
        if isinstance(details, dict):
            detail_text = json.dumps(details, sort_keys=True, ensure_ascii=False)
        else:
            detail_text = json.dumps({"message": str(details)[:3000]}, ensure_ascii=False)
        conn.execute(
            "INSERT INTO job_events(job_id, from_status, to_status, event, details) VALUES (?,?,?,?,?)",
            (job_id, from_status, to_status, event_name, detail_text[:4000]),
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
        resource_units: int | None = None,
        resource_class: str | None = None,
        reserved_disk_bytes: int | None = None,
        reserved_memory_bytes: int | None = None,
        available_disk_bytes: int | None = None,
        resource_capacity_units: int | None = None,
        max_reserved_memory_bytes: int | None = None,
        attempt_id: str | None = None,
        worker_token: str | None = None,
        workspace_dir: str | None = None,
    ) -> None:
        """Insert a queued job while enforcing admission limits in one transaction."""
        encoded = json.dumps(params)

        def write(conn: sqlite3.Connection) -> None:
            conn.commit()
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
                count = int(conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE principal=? AND status IN ('queued','running')",
                    (str(principal).strip() or "unknown",),
                ).fetchone()[0])
                if count >= int(principal_limit):
                    raise JobAdmissionError("principal queue capacity reached")
            if resource_units is not None:
                used_units = int(conn.execute(
                    "SELECT COALESCE(SUM(resource_units),0) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0])
                if used_units + int(resource_units) > int(resource_capacity_units or 100):
                    raise JobAdmissionError("resource capacity reached")
            requested_class = str(resource_class).strip().upper() if resource_class else None
            class_name = requested_class or "STANDARD"
            if requested_class is not None:
                class_limit = self._resource_class_capacity(class_name)
                if self._resource_class_active_count(conn, class_name) >= class_limit:
                    raise JobAdmissionError(f"resource class {class_name} capacity reached")
            if reserved_memory_bytes is not None and max_reserved_memory_bytes is not None:
                used_memory = int(conn.execute(
                    "SELECT COALESCE(SUM(reserved_memory_bytes),0) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0])
                if used_memory + int(reserved_memory_bytes) > int(max_reserved_memory_bytes):
                    raise JobAdmissionError("reserved memory capacity reached")
            if reserved_disk_bytes is not None and available_disk_bytes is not None:
                used_disk = int(conn.execute(
                    "SELECT COALESCE(SUM(reserved_disk_bytes),0) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0])
                if used_disk + int(reserved_disk_bytes) > int(available_disk_bytes):
                    raise JobAdmissionError("reserved disk capacity reached")
            conn.execute(
                "INSERT INTO jobs (id, topic, status, step, params, principal, attempt_id, worker_token, "
                "workspace_dir, resource_units, resource_class, reserved_disk_bytes, reserved_memory_bytes, created_at, updated_at) "
                "VALUES (?, ?, 'queued', 'waiting', ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (
                    job_id, topic, encoded, str(principal).strip() or "unknown",
                    attempt_id, worker_token, workspace_dir,
                    int(resource_units or 1), class_name,
                    int(reserved_disk_bytes or 0), int(reserved_memory_bytes or 0),
                ),
            )
            self._record_event(conn, job_id, "JOB_CREATED", to_status="queued", details={"topic": topic[:200]})

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
        resource_units: int | None = None,
        resource_class: str | None = None,
        reserved_disk_bytes: int | None = None,
        reserved_memory_bytes: int | None = None,
        available_disk_bytes: int | None = None,
        resource_capacity_units: int | None = None,
        max_reserved_memory_bytes: int | None = None,
        attempt_id: str | None = None,
        worker_token: str | None = None,
        workspace_dir: str | None = None,
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
            conn.commit()
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
                count = int(conn.execute(
                    "SELECT COUNT(*) FROM jobs WHERE principal=? AND status IN ('queued','running')",
                    (principal_value,),
                ).fetchone()[0])
                if count >= int(principal_limit):
                    raise JobAdmissionError("principal queue capacity reached")
            if resource_units is not None:
                used_units = int(conn.execute(
                    "SELECT COALESCE(SUM(resource_units),0) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0])
                if used_units + int(resource_units) > int(resource_capacity_units or 100):
                    raise JobAdmissionError("resource capacity reached")
            requested_class = str(resource_class).strip().upper() if resource_class else None
            class_name = requested_class or "STANDARD"
            if requested_class is not None:
                class_limit = self._resource_class_capacity(class_name)
                if self._resource_class_active_count(conn, class_name) >= class_limit:
                    raise JobAdmissionError(f"resource class {class_name} capacity reached")
            if reserved_memory_bytes is not None and max_reserved_memory_bytes is not None:
                used_memory = int(conn.execute(
                    "SELECT COALESCE(SUM(reserved_memory_bytes),0) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0])
                if used_memory + int(reserved_memory_bytes) > int(max_reserved_memory_bytes):
                    raise JobAdmissionError("reserved memory capacity reached")
            if reserved_disk_bytes is not None and available_disk_bytes is not None:
                used_disk = int(conn.execute(
                    "SELECT COALESCE(SUM(reserved_disk_bytes),0) FROM jobs WHERE status IN ('queued','running')"
                ).fetchone()[0])
                if used_disk + int(reserved_disk_bytes) > int(available_disk_bytes):
                    raise JobAdmissionError("reserved disk capacity reached")
            conn.execute(
                "INSERT INTO idempotency_keys(principal, idem_key, request_hash, job_id) VALUES (?,?,?,?)",
                (principal_value, key, fingerprint, job_id),
            )
            conn.execute(
                "INSERT INTO jobs (id, topic, status, step, params, principal, attempt_id, worker_token, "
                "workspace_dir, resource_units, resource_class, reserved_disk_bytes, reserved_memory_bytes, created_at, updated_at) "
                "VALUES (?, ?, 'queued', 'waiting', ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (
                    job_id, topic, encoded, str(principal).strip() or "unknown",
                    attempt_id, worker_token, workspace_dir,
                    int(resource_units or 1), class_name,
                    int(reserved_disk_bytes or 0), int(reserved_memory_bytes or 0),
                ),
            )
            self._record_event(conn, job_id, "JOB_CREATED", to_status="queued", details={"idempotent": True})
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
                event_name = {
                    "running": "JOB_STARTED",
                    "cancelling": "JOB_CANCEL_REQUESTED",
                    "cancelled": "JOB_CANCELLED",
                    "error": "JOB_FAILED",
                    "done": "JOB_COMPLETED",
                    "interrupted": "JOB_RECOVERED",
                }.get(target_status, "JOB_STATUS_CHANGED")
                self._record_event(
                    conn,
                    job_id,
                    event_name,
                    from_status=previous_status,
                    to_status=target_status,
                    details={"step": kwargs.get("step"), "error_code": kwargs.get("error_code")},
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
    def claim_job(
        self,
        job_id: str,
        *,
        attempt_id: str | None = None,
        worker_id: str | None = None,
        lease_token: str | None = None,
        workspace_dir: str | None = None,
    ) -> bool:
        """Atomically claim a queued job and optionally bind it to a fenced attempt."""
        def write(conn: sqlite3.Connection) -> int:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or str(row["status"]) != "queued":
                return 0
            validate_transition("queued", "running")
            changed = int(conn.execute(
                "UPDATE jobs SET status='running', step='starting', "
                "worker_heartbeat_at=CURRENT_TIMESTAMP, started_at=CURRENT_TIMESTAMP, finished_at=NULL, "
                "current_attempt=?, attempt_id=?, worker_token=?, workspace_dir=?, error=NULL, error_code=NULL, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",
                (attempt_id, attempt_id, lease_token, workspace_dir, job_id),
            ).rowcount)
            if changed and attempt_id and worker_id and lease_token and workspace_dir:
                next_number = int(conn.execute(
                    "SELECT COALESCE(MAX(attempt_number),0)+1 FROM job_attempts WHERE job_id=?",
                    (job_id,),
                ).fetchone()[0])
                conn.execute(
                    "INSERT INTO job_attempts(id,job_id,attempt_number,worker_id,lease_token,workspace_dir) "
                    "VALUES (?,?,?,?,?,?)",
                    (attempt_id, job_id, next_number, worker_id, lease_token, workspace_dir),
                )
            if changed:
                self._record_event(
                    conn, job_id, "JOB_CLAIMED", from_status="queued",
                    to_status="running", details=f"attempt_id={attempt_id or ''}",
                )
            return changed
        return int(self.write(write)) == 1

    def heartbeat_job(
        self,
        job_id: str,
        *,
        attempt_id: str | None = None,
        lease_token: str | None = None,
    ) -> bool:
        """Refresh the durable worker lease, fenced to the active attempt when supplied."""
        def write(conn: sqlite3.Connection) -> int:
            if attempt_id and lease_token:
                changed = int(conn.execute(
                    "UPDATE jobs SET worker_heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status IN ('running','cancelling') AND attempt_id=? AND worker_token=?",
                    (job_id, attempt_id, lease_token),
                ).rowcount)
                if changed:
                    conn.execute(
                        "UPDATE job_attempts SET heartbeat_at=CURRENT_TIMESTAMP WHERE id=? AND lease_token=?",
                        (attempt_id, lease_token),
                    )
                return changed
            return int(conn.execute(
                "UPDATE jobs SET worker_heartbeat_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status IN ('running','cancelling')",
                (job_id,),
            ).rowcount)
        return bool(self.write(write))

    def can_publish_attempt(self, job_id: str, attempt_id: str, lease_token: str) -> bool:
        """Return true only while the fenced attempt still owns a running job."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE id=? AND attempt_id=? AND worker_token=? "
                "AND status='running'",
                (job_id, attempt_id, lease_token),
            ).fetchone()
        return row is not None

    def has_attempt_ownership(self, job_id: str, attempt_id: str, lease_token: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE id=? AND attempt_id=? AND worker_token=?",
                (job_id, attempt_id, lease_token),
            ).fetchone()
        return row is not None

    def is_attempt_owner(self, job_id: str, attempt_id: str, lease_token: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE id=? AND attempt_id=? AND worker_token=? "
                "AND status IN ('running','cancelling')",
                (job_id, attempt_id, lease_token),
            ).fetchone()
            return row is not None

    def update_job_if_owned(
        self,
        job_id: str,
        attempt_id: str,
        lease_token: str,
        **kwargs: Any,
    ) -> bool:
        allowed = {"status", "step", "params", "pkg_dir", "error", "error_code", "workspace_dir"}
        invalid = set(kwargs) - allowed
        if invalid or not kwargs:
            raise ValueError(f"Invalid owned job update fields: {sorted(invalid)}")
        def write(conn: sqlite3.Connection) -> bool:
            row = conn.execute(
                "SELECT status FROM jobs WHERE id=? AND attempt_id=? AND worker_token=? "
                "AND status IN ('running','cancelling')",
                (job_id, attempt_id, lease_token),
            ).fetchone()
            if row is None:
                return False
            current = str(row["status"])
            target = str(kwargs.get("status", current))
            if target != current:
                validate_transition(current, target)
            fields = ", ".join(f"{key}=?" for key in kwargs)
            changed = conn.execute(
                f"UPDATE jobs SET {fields}, "
                "finished_at=CASE WHEN ? IN ('done','error','interrupted','cancelled') "
                "THEN COALESCE(finished_at, CURRENT_TIMESTAMP) ELSE finished_at END, "
                "updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND attempt_id=? AND worker_token=?",
                [*kwargs.values(), kwargs.get("status"), job_id, attempt_id, lease_token],
            ).rowcount
            if changed:
                conn.execute(
                    "UPDATE job_attempts SET status=?, error_code=?, finished_at=CASE WHEN ? IN "
                    "('done','error','interrupted','cancelled') THEN CURRENT_TIMESTAMP ELSE finished_at END "
                    "WHERE id=? AND lease_token=?",
                    (target, kwargs.get("error_code"), target, attempt_id, lease_token),
                )
            return bool(changed)
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
                "SELECT status, error, error_code, retry_count FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                return 0
            status = str(row["status"])
            if status not in {"error", "interrupted"}:
                raise JobRetryNotAllowed("only failed or interrupted jobs can be retried")
            attempts = int(row["retry_count"] or 0)
            if attempts >= max_attempts:
                raise JobRetryNotAllowed("maximum retry attempts reached")
            if status == "error":
                from ai_video_factory.retry_policy import is_retryable_code
                code_result = is_retryable_code(row["error_code"])
                if code_result is False:
                    raise JobRetryNotAllowed("recorded job failure is deterministic and should not be retried")
                if code_result is None and row["error"] and not is_retryable_error(RuntimeError(str(row["error"]))):
                    raise JobRetryNotAllowed("recorded job failure is deterministic and should not be retried")
            changed = int(conn.execute(
                "UPDATE jobs SET status='queued', step='waiting', error=NULL, error_code=NULL, pkg_dir=NULL, "
                "attempt_id=NULL, worker_token=NULL, workspace_dir=NULL, current_attempt=NULL, "
                "worker_heartbeat_at=NULL, started_at=NULL, finished_at=NULL, retry_count=retry_count+1, "
                "updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status IN ('error','interrupted') AND retry_count<?",
                (job_id, max_attempts),
            ).rowcount)
            if changed:
                try:
                    from ai_video_factory.observability_metrics import GLOBAL_METRICS
                    GLOBAL_METRICS.increment("jobs_retried_total")
                except Exception:
                    pass
                self._record_event(
                    conn,
                    job_id,
                    "JOB_RETRY_SCHEDULED",
                    from_status=status,
                    to_status="queued",
                    details={"attempt": attempts + 1, "error_code": row["error_code"]},
                )
            return changed

        return int(self.write(write))

    def recover_nonterminal_jobs(self) -> int:
        """Fence all jobs left non-terminal by a previous web-process crash."""
        def write(conn: sqlite3.Connection) -> int:
            rows = conn.execute(
                "SELECT id, status, attempt_id FROM jobs "
                "WHERE status IN ('running','cancelling')"
            ).fetchall()
            changed = 0
            for row in rows:
                job_id = str(row["id"])
                attempt_id = row["attempt_id"]
                updated = conn.execute(
                    "UPDATE jobs SET status='interrupted', step='interrupted', "
                    "finished_at=CURRENT_TIMESTAMP, attempt_id=NULL, worker_token=NULL, "
                    "workspace_dir=NULL, current_attempt=NULL, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=? AND status IN ('running','cancelling')",
                    (job_id,),
                ).rowcount
                if not updated:
                    continue
                changed += int(updated)
                if attempt_id:
                    conn.execute(
                        "UPDATE job_attempts SET status='interrupted', finished_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND status='running'",
                        (attempt_id,),
                    )
                self._record_event(
                    conn,
                    job_id,
                    "JOB_RECOVERED",
                    from_status=str(row["status"]),
                    to_status="interrupted",
                    details={"attempt_id": attempt_id},
                )
            return changed

        return int(self.write(write))

    def get_job(self, job_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_queued_jobs(self, limit: int = 100) -> list[dict]:
        bounded_limit = max(1, min(int(limit), self.MAX_LIST_LIMIT))
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status='queued' "
                "ORDER BY priority DESC, created_at ASC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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

    def record_job_event(
        self,
        job_id: str,
        event: str,
        *,
        from_status: str | None = None,
        to_status: str | None = None,
        details: Any = None,
    ) -> None:
        self.write(lambda conn: self._record_event(
            conn,
            job_id,
            event,
            from_status=from_status,
            to_status=to_status,
            details=details or {},
        ))

    def record_audit_event(
        self,
        principal: str,
        action: str,
        resource: str,
        *,
        resource_id: str | None = None,
        result: str = "success",
        metadata: str = "",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.write(lambda conn: conn.execute(
            "INSERT INTO audit_events("
            "principal, action, resource, resource_id, result, metadata, ip_address, user_agent"
            ") VALUES (?,?,?,?,?,?,?,?)",
            (
                str(principal).strip() or "unknown",
                str(action).strip() or "unknown",
                str(resource).strip() or "unknown",
                resource_id,
                str(result).strip() or "success",
                str(metadata)[:10000],
                str(ip_address or "")[:128],
                str(user_agent or "")[:512],
            ),
        ))

    def register_worker(
        self,
        worker_id: str,
        pid: int,
        *,
        status: str = "running",
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        payload = json.dumps(capabilities or {}, sort_keys=True)
        import socket
        self.write(lambda conn: conn.execute(
            "INSERT INTO workers(id, hostname, pid, status, last_heartbeat_at, capabilities) "
            "VALUES (?,?,?,?,CURRENT_TIMESTAMP,?) "
            "ON CONFLICT(id) DO UPDATE SET hostname=excluded.hostname, pid=excluded.pid, "
            "status=excluded.status, last_heartbeat_at=CURRENT_TIMESTAMP, capabilities=excluded.capabilities",
            (str(worker_id), socket.gethostname()[:255], int(pid), str(status)[:32], payload[:4000]),
        ))

    def heartbeat_worker(self, worker_id: str) -> bool:
        return bool(self.write(lambda conn: conn.execute(
            "UPDATE workers SET last_heartbeat_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(worker_id),),
        ).rowcount))

    def set_worker_status(self, worker_id: str, status: str, *, pid: int | None = None) -> bool:
        return bool(self.write(lambda conn: conn.execute(
            "UPDATE workers SET status=?, pid=COALESCE(?,pid), last_heartbeat_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(status)[:32], pid, str(worker_id)),
        ).rowcount))

    def record_artifact(
        self,
        job_id: str,
        kind: str,
        path: str,
        *,
        attempt_id: str | None = None,
        sha256: str | None = None,
        size_bytes: int | None = None,
    ) -> None:
        resolved_size = int(size_bytes if size_bytes is not None else Path(path).stat().st_size)
        self.write(lambda conn: conn.execute(
            "INSERT INTO job_artifacts(job_id, attempt_id, kind, path, sha256, size_bytes) VALUES (?,?,?,?,?,?)",
            (str(job_id), attempt_id, str(kind)[:120], str(path), sha256, resolved_size),
        ))

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
