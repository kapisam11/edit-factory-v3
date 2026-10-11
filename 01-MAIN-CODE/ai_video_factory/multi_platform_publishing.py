"""Durable per-platform/account publication queue for existing local videos.

This shares the folder publisher's immutable staging directory and SQLite file.
Each configured destination gets an independent state machine, retry schedule,
remote operation ID, event history, and terminal result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

from .folder_publisher import FolderPublisher, FolderPublisherConfig, _sha256
from .platform_adapters import PlatformPublishError, PublishResult, adapter_for
from .retry_policy import retry_after

LOG = logging.getLogger(__name__)
SUPPORTED_PLATFORMS = {"youtube", "instagram", "tiktok"}
FINAL_STATES = {"published", "permanent_failed", "cancelled"}
ACTIVE_STATES = {"uploading", "processing_remote"}
TARGET_PATTERN = re.compile(r"^(youtube|instagram|tiktok):([A-Za-z0-9_.-]{1,64})$", re.IGNORECASE)


@dataclass(frozen=True)
class Target:
    platform: str
    account_id: str

    @classmethod
    def parse(cls, value: str) -> "Target":
        match = TARGET_PATTERN.fullmatch(str(value or "").strip())
        if not match:
            raise ValueError(
                "each destination must use platform:account syntax, e.g. youtube:default"
            )
        platform, account = match.groups()
        normalized = platform.lower()
        if normalized not in SUPPORTED_PLATFORMS:
            raise ValueError(f"unsupported publishing platform: {platform}")
        return cls(normalized, account)


def publication_key(metadata: dict[str, Any], *, source_path: str, source_sha256: str) -> str:
    supplied = str(metadata.get("publication_id") or "").strip()
    if supplied:
        if len(supplied) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", supplied):
            raise ValueError("publication_id must use 1-128 letters, digits, dot, underscore, colon or hyphen")
        return supplied
    # Legacy sidecars without an ID remain stable. The path is intentionally
    # part of the identity: equal hashes at different paths may be posted twice.
    digest = hashlib.sha256(f"{Path(source_path).resolve()}\0{source_sha256}".encode("utf-8")).hexdigest()
    return f"legacy-{digest[:40]}"


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, requests.RequestException):
        return type(exc).__name__
    return " ".join(str(exc).split())[:1200] or type(exc).__name__


class PublicationStore:
    """SQLite state store using transactional claims and lease fencing."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=20000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS platform_publications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    publication_id TEXT NOT NULL,
                    source_item_id INTEGER NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    staged_path TEXT NOT NULL,
                    source_size_bytes INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    sidecar_mtime_ns INTEGER,
                    platform TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    adapter_state_json TEXT NOT NULL DEFAULT '{}',
                    remote_operation_id TEXT,
                    remote_post_id TEXT,
                    remote_url TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_until REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    published_at REAL,
                    error_code TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    UNIQUE(publication_id, platform, account_id)
                );
                CREATE INDEX IF NOT EXISTS platform_publications_due
                    ON platform_publications(state, next_attempt_at, created_at);
                CREATE INDEX IF NOT EXISTS platform_publications_account_day
                    ON platform_publications(platform, account_id, published_at);
                CREATE INDEX IF NOT EXISTS platform_publications_source
                    ON platform_publications(source_item_id, platform, account_id);
                CREATE TABLE IF NOT EXISTS platform_publication_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    publication_job_id INTEGER NOT NULL,
                    occurred_at REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(publication_job_id)
                        REFERENCES platform_publications(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS platform_publication_events_job
                    ON platform_publication_events(publication_job_id, id);
                CREATE TABLE IF NOT EXISTS platform_publication_notifications (
                    event_key TEXT PRIMARY KEY,
                    notified_at REAL NOT NULL,
                    response_code INTEGER
                );
                """
            )
            try:
                os.chmod(self.database_path, 0o600)
            except OSError:
                # Windows ACLs are inherited from the state directory; document
                # that state must be writable/readable only by the service user.
                pass

    def _event(
        self,
        connection: sqlite3.Connection,
        job_id: int,
        event_type: str,
        state: str,
        detail: dict[str, Any] | None = None,
        *,
        now: float | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO platform_publication_events "
            "(publication_job_id,occurred_at,event_type,state,detail_json) VALUES (?,?,?,?,?)",
            (job_id, time.time() if now is None else now, event_type, state, _compact_json(detail or {})),
        )

    def enqueue(
        self,
        *,
        publication_id: str,
        item: dict[str, Any],
        source_sha256: str,
        staged_path: Path,
        target: Target,
        metadata: dict[str, Any],
        max_attempts: int,
        now: float,
    ) -> tuple[int, bool]:
        idempotency = hashlib.sha256(
            f"edit-factory-v1\0{target.platform}\0{target.account_id}\0{publication_id}\0{source_sha256}".encode(
                "utf-8"
            )
        ).hexdigest()
        metadata_text = _compact_json(metadata)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id,source_sha256,state FROM platform_publications "
                "WHERE publication_id=? AND platform=? AND account_id=?",
                (publication_id, target.platform, target.account_id),
            ).fetchone()
            if existing is not None:
                if str(existing["source_sha256"]) != source_sha256:
                    connection.rollback()
                    raise ValueError(
                        "publication_id is already bound to different media; choose a new publication_id for an intentional repost"
                    )
                return int(existing["id"]), False
            cursor = connection.execute(
                """
                INSERT INTO platform_publications
                    (publication_id,source_item_id,source_path,source_sha256,staged_path,
                     source_size_bytes,source_mtime_ns,sidecar_mtime_ns,platform,account_id,
                     metadata_json,idempotency_key,state,attempts,max_attempts,next_attempt_at,
                     created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'queued',0,?,0,?,?)
                """,
                (
                    publication_id,
                    int(item["id"]),
                    str(item["path"]),
                    source_sha256,
                    str(staged_path),
                    int(item["size_bytes"]),
                    int(item["mtime_ns"]),
                    item.get("sidecar_mtime_ns"),
                    target.platform,
                    target.account_id,
                    metadata_text,
                    idempotency,
                    max(1, min(int(max_attempts), 20)),
                    now,
                    now,
                ),
            )
            job_id = int(cursor.lastrowid)
            self._event(
                connection,
                job_id,
                "queued",
                "queued",
                {"platform": target.platform, "account_id": target.account_id},
                now=now,
            )
            connection.commit()
        return job_id, True

    def jobs(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id,publication_id,source_item_id,source_path,source_sha256,platform,"
                "account_id,state,remote_operation_id,remote_post_id,remote_url,attempts,"
                "max_attempts,next_attempt_at,created_at,updated_at,published_at,error_code,"
                "last_error FROM platform_publications ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 5000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state,COUNT(*) AS count FROM platform_publications GROUP BY state"
            ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    def history(self, job_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT occurred_at,event_type,state,detail_json FROM platform_publication_events "
                "WHERE publication_job_id=? ORDER BY id DESC LIMIT ?",
                (int(job_id), max(1, min(int(limit), 1000))),
            ).fetchall()
        return [
            {
                "occurred_at": float(row["occurred_at"]),
                "event_type": str(row["event_type"]),
                "state": str(row["state"]),
                "detail": json.loads(str(row["detail_json"] or "{}")),
            }
            for row in rows
        ]

    def claim_next(self, *, max_per_day: int, now: float) -> dict[str, Any] | None:
        if max_per_day < 1:
            return None
        day_start = now - (now % 86400)
        owner = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM platform_publications
                WHERE (
                    state='queued'
                    OR (state='retryable_failed' AND next_attempt_at<=?)
                    OR (state IN ('uploading','processing_remote') AND COALESCE(lease_until,0)<=?
                        AND next_attempt_at<=?)
                )
                AND NOT EXISTS (
                    SELECT 1 FROM platform_publications sent
                    WHERE sent.platform=platform_publications.platform
                      AND sent.account_id=platform_publications.account_id
                      AND sent.state='published' AND sent.published_at>=?
                )
                AND NOT EXISTS (
                    SELECT 1 FROM platform_publications active
                    WHERE active.platform=platform_publications.platform
                      AND active.account_id=platform_publications.account_id
                      AND active.id<>platform_publications.id
                      AND active.state IN ('uploading','processing_remote')
                      AND COALESCE(active.lease_until,0)>?
                )
                ORDER BY CASE state WHEN 'processing_remote' THEN 0 WHEN 'uploading' THEN 1 ELSE 2 END,
                         next_attempt_at,created_at,id
                LIMIT 1
                """,
                (now, now, now, day_start, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            job_id = int(row["id"])
            new_state = (
                "processing_remote"
                if str(row["remote_operation_id"] or "")
                else "uploading"
            )
            cursor = connection.execute(
                "UPDATE platform_publications SET state=?,attempts=attempts+1,lease_owner=?,"
                "lease_until=?,updated_at=?,last_error='',error_code=NULL "
                "WHERE id=? AND COALESCE(lease_until,0)<=?",
                (new_state, owner, now + 600, now, job_id, now),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            self._event(
                connection,
                job_id,
                "attempt_started",
                new_state,
                {"attempt": int(row["attempts"]) + 1},
                now=now,
            )
            claimed = connection.execute(
                "SELECT * FROM platform_publications WHERE id=?", (job_id,)
            ).fetchone()
            connection.commit()
        result = dict(claimed)
        result["lease_owner"] = owner
        return result

    def renew_lease(self, job_id: int, owner: str, *, now: float) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE platform_publications SET lease_until=?,updated_at=? "
                "WHERE id=? AND state IN ('uploading','processing_remote') AND lease_owner=?",
                (now + 600, now, job_id, owner),
            )
        return cursor.rowcount == 1

    def record_operation(
        self,
        job_id: int,
        owner: str,
        operation_id: str,
        adapter_state: dict[str, Any],
        state: str,
        *,
        now: float,
    ) -> None:
        if state not in ACTIVE_STATES:
            raise ValueError(f"invalid active publication state: {state}")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE platform_publications SET remote_operation_id=?,adapter_state_json=?,"
                "state=?,updated_at=?,lease_until=? WHERE id=? AND lease_owner=?",
                (operation_id, _compact_json(adapter_state), state, now, now + 600, job_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("publication lease was lost before saving the remote operation ID")
            self._event(
                connection,
                job_id,
                "remote_operation_saved",
                state,
                {"remote_operation_id_present": bool(operation_id)},
                now=now,
            )

    def save_adapter_state(
        self,
        job_id: int,
        owner: str,
        adapter_state: dict[str, Any],
        state: str,
        *,
        now: float,
    ) -> None:
        if state not in ACTIVE_STATES:
            raise ValueError(f"invalid active publication state: {state}")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE platform_publications SET adapter_state_json=?,state=?,updated_at=?,"
                "lease_until=? WHERE id=? AND lease_owner=?",
                (_compact_json(adapter_state), state, now, now + 600, job_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("publication lease was lost while saving adapter progress")
            self._event(connection, job_id, "adapter_progress", state, {}, now=now)

    def save_remote_post(
        self,
        job_id: int,
        owner: str,
        remote_post_id: str,
        remote_url: str,
        *,
        now: float,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE platform_publications SET remote_post_id=COALESCE(NULLIF(?,''),remote_post_id),"
                "remote_url=CASE WHEN ?<>'' THEN ? ELSE remote_url END,updated_at=?,lease_until=? "
                "WHERE id=? AND lease_owner=?",
                (remote_post_id, remote_url, remote_url, now, now + 600, job_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("publication lease was lost after remote post ID was received")
            self._event(
                connection,
                job_id,
                "remote_post_id_saved",
                "processing_remote",
                {"remote_post_id_present": bool(remote_post_id), "remote_url_present": bool(remote_url)},
                now=now,
            )

    def mark_published(
        self,
        job_id: int,
        owner: str,
        result: PublishResult,
        *,
        now: float,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE platform_publications SET state='published',"
                "remote_operation_id=COALESCE(NULLIF(?,''),remote_operation_id),"
                "remote_post_id=COALESCE(NULLIF(?,''),remote_post_id),"
                "remote_url=CASE WHEN ?<>'' THEN ? ELSE remote_url END,"
                "published_at=?,updated_at=?,lease_owner=NULL,lease_until=NULL,next_attempt_at=0,"
                "last_error='',error_code=NULL WHERE id=? AND lease_owner=?",
                (
                    result.remote_operation_id or "",
                    result.remote_post_id or "",
                    result.remote_url,
                    result.remote_url,
                    now,
                    now,
                    job_id,
                    owner,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("publication lease was lost; remote post was not marked complete")
            self._event(connection, job_id, "published", "published", {}, now=now)

    def mark_failure(
        self,
        job: dict[str, Any],
        owner: str,
        error: Exception,
        *,
        now: float,
    ) -> str:
        attempts = int(job["attempts"])
        max_attempts = int(job["max_attempts"])
        remote_operation_id = str(job.get("remote_operation_id") or "")
        if isinstance(error, PlatformPublishError):
            code = error.code
            retryable = error.retryable
            uncertain = error.uncertain
            message = _safe_error_message(error)
        else:
            code = type(error).__name__.upper()
            message = _safe_error_message(error)
            lowered = message.lower()
            retryable = any(
                token in lowered
                for token in ("timeout", "connection", "429", "rate limit", "temporarily", "502", "503", "504")
            )
            uncertain = retryable

        if retryable and attempts < max_attempts:
            delay = retry_after(attempts, base=30.0, cap=3600.0, key=str(job["idempotency_key"]))
            state = "processing_remote" if remote_operation_id and uncertain else "retryable_failed"
            next_attempt = now + delay
        else:
            state = "permanent_failed"
            next_attempt = 0.0

        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE platform_publications SET state=?,next_attempt_at=?,lease_owner=NULL,"
                "lease_until=NULL,updated_at=?,error_code=?,last_error=? "
                "WHERE id=? AND lease_owner=?",
                (state, next_attempt, now, code[:100], message[:1200], int(job["id"]), owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("publication lease was lost while recording a failure")
            self._event(
                connection,
                int(job["id"]),
                "failure_recorded",
                state,
                {
                    "error_code": code[:100],
                    "retryable": bool(retryable and attempts < max_attempts),
                    "uncertain": bool(uncertain),
                    "retry_delay_seconds": round(next_attempt - now, 3) if next_attempt else 0,
                },
                now=now,
            )
        return state

    def claim_notification(self, event_key: str, *, now: float) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO platform_publication_notifications(event_key,notified_at) VALUES(?,?)",
                (event_key, now),
            )
        return cursor.rowcount == 1

    def set_notification_response(self, event_key: str, status_code: int | None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE platform_publication_notifications SET response_code=? WHERE event_key=?",
                (status_code, event_key),
            )


class MultiPlatformPublisher:
    def __init__(
        self,
        folder: FolderPublisher,
        *,
        publish_enabled: bool = False,
        max_per_account_per_day: int = 2,
        max_attempts: int = 5,
        min_free_disk_bytes: int = 2 * 1024**3,
    ) -> None:
        self.folder = folder
        self.store = PublicationStore(folder.db_path)
        self.publish_enabled = bool(publish_enabled)
        self.max_per_account_per_day = max(0, int(max_per_account_per_day))
        self.max_attempts = max(1, min(int(max_attempts), 20))
        self.min_free_disk_bytes = max(0, int(min_free_disk_bytes))
        self.heartbeat_path = Path(
            os.environ.get(
                "AIVF_PUBLISH_HEARTBEAT_PATH",
                str(folder.db_path.parent / "publishing-heartbeat.json"),
            )
        ).expanduser()
        self.last_success_epoch: float | None = None

    def scan_and_enqueue(self) -> dict[str, Any]:
        scan_result = self.folder.scan()
        queued = 0
        existing = 0
        rejected: list[dict[str, Any]] = []
        with self.folder.store._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM folder_items WHERE state='READY' ORDER BY discovered_at,id"
            ).fetchall()
        for raw in rows:
            item = dict(raw)
            try:
                source_path = Path(str(item["path"])).resolve(strict=True)
                staged_path = Path(str(item.get("staged_path") or "")).resolve(strict=True)
                staged_path.relative_to(self.folder.staging_dir.resolve())
                source_hash = str(item.get("source_sha256") or "")
                if not source_hash or not staged_path.is_file() or staged_path.is_symlink():
                    raise ValueError("ready item does not have a safe immutable media snapshot")
                if _sha256(staged_path) != source_hash:
                    raise ValueError("staged media snapshot failed SHA-256 validation")
                metadata = json.loads(str(item.get("metadata_json") or "{}"))
                if not isinstance(metadata, dict):
                    raise ValueError("publish metadata must be a JSON object")
                targets_raw = metadata.get("destinations", ["youtube:default"])
                if not isinstance(targets_raw, list) or not targets_raw:
                    raise ValueError("destinations must be a non-empty list")
                targets = list(dict.fromkeys(Target.parse(str(value)) for value in targets_raw))
                pub_id = publication_key(
                    metadata,
                    source_path=str(source_path),
                    source_sha256=source_hash,
                )
                try:
                    probe = json.loads(str(item.get("probe_json") or "{}"))
                except (json.JSONDecodeError, TypeError):
                    probe = {}
                for target in targets:
                    target_metadata = dict(metadata)
                    overrides = metadata.get("platform_overrides")
                    override = overrides.get(f"{target.platform}:{target.account_id}", {}) if isinstance(overrides, dict) else {}
                    if not isinstance(override, dict):
                        raise ValueError(f"platform_overrides for {target.platform}:{target.account_id} must be an object")
                    target_metadata["_platform_override"] = override
                    target_metadata["_media_probe"] = probe if isinstance(probe, dict) else {}
                    self._resolve_media_assets(target_metadata, source_path)
                    _, created = self.store.enqueue(
                        publication_id=pub_id,
                        item=item,
                        source_sha256=source_hash,
                        staged_path=staged_path,
                        target=target,
                        metadata=target_metadata,
                        max_attempts=self.max_attempts,
                        now=time.time(),
                    )
                    if created:
                        queued += 1
                    else:
                        existing += 1
            except (OSError, sqlite3.Error, ValueError, RuntimeError, json.JSONDecodeError) as exc:
                rejected.append({"item_id": int(item["id"]), "reason": _safe_error_message(exc)})
        result = {
            "scan": scan_result,
            "publication_jobs_added": queued,
            "publication_jobs_already_present": existing,
            "rejected_ready_items": rejected,
            "publication_counts": self.store.counts(),
        }
        self._write_heartbeat()
        return result

    def _resolve_media_assets(self, metadata: dict[str, Any], source_path: Path) -> None:
        for field in ("thumbnail_path", "caption_path"):
            raw = str(metadata.get(field) or "").strip()
            if not raw:
                continue
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = source_path.parent / candidate
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.folder.watch_dir.resolve())
            if not resolved.is_file() or resolved.is_symlink():
                raise ValueError(f"{field} must refer to a regular file within the watch directory")
            metadata[field] = str(resolved)

    def _validate_before_first_attempt(self, job: dict[str, Any]) -> None:
        with self.folder.store._connect() as connection:
            row = connection.execute(
                "SELECT state,size_bytes,mtime_ns,sidecar_mtime_ns,path FROM folder_items WHERE id=?",
                (int(job["source_item_id"]),),
            ).fetchone()
        if row is None or str(row["state"]) != "READY":
            raise PlatformPublishError(
                "source item is no longer in the reviewed READY state; rescan and approve it again",
                code="SOURCE_NOT_APPROVED",
                retryable=False,
            )
        source = Path(str(row["path"]))
        try:
            current = source.stat()
            unchanged = (
                current.st_size == int(row["size_bytes"])
                and current.st_mtime_ns == int(row["mtime_ns"])
                and _sha256(source) == str(job["source_sha256"])
                and source.resolve(strict=True).is_relative_to(self.folder.watch_dir.resolve())
            )
        except (OSError, ValueError):
            unchanged = False
        if not unchanged:
            raise PlatformPublishError(
                "source changed or disappeared after review; create a new approval for the new bytes",
                code="SOURCE_CHANGED",
                retryable=False,
            )
        sidecar = Path(str(source) + ".publish.json")
        try:
            sidecar_unchanged = (
                not sidecar.is_symlink()
                and sidecar.is_file()
                and row["sidecar_mtime_ns"] is not None
                and sidecar.stat().st_mtime_ns == int(row["sidecar_mtime_ns"])
            )
        except OSError:
            sidecar_unchanged = False
        if not sidecar_unchanged:
            raise PlatformPublishError(
                "approval sidecar changed after review; rescan and reconfirm the approval",
                code="SIDECAR_CHANGED",
                retryable=False,
            )

    def process_one(self, *, now: float | None = None) -> dict[str, Any] | None:
        current_time = time.time() if now is None else float(now)
        if not self.publish_enabled:
            return None
        try:
            free_bytes = shutil.disk_usage(self.folder.staging_dir).free
        except OSError as exc:
            LOG.error("cannot inspect staging disk usage: %s", type(exc).__name__)
            return None
        if free_bytes < self.min_free_disk_bytes:
            LOG.error(
                "publishing is paused: free disk %d bytes is below the configured %d-byte floor",
                free_bytes,
                self.min_free_disk_bytes,
            )
            self._write_heartbeat(disk_free_bytes=free_bytes, status="blocked_low_disk")
            self._notify_system_failure(
                "publishing:low-disk",
                "Publishing paused because disk space is below the configured safety floor.",
                {"free_disk_bytes": free_bytes, "required_free_disk_bytes": self.min_free_disk_bytes},
            )
            return None

        job = self.store.claim_next(
            max_per_day=self.max_per_account_per_day,
            now=current_time,
        )
        if job is None:
            self._write_heartbeat(disk_free_bytes=free_bytes)
            return None
        stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._renew_lease_loop,
            args=(int(job["id"]), str(job["lease_owner"]), stop),
            name=f"publication-lease-{job['id']}",
            daemon=True,
        )
        heartbeat.start()
        try:
            media_path = Path(str(job["staged_path"])).resolve(strict=True)
            media_path.relative_to(self.folder.staging_dir.resolve())
            if not media_path.is_file() or media_path.is_symlink():
                raise PlatformPublishError(
                    "staged source is missing or is not a regular file",
                    code="STAGED_MEDIA_MISSING",
                    retryable=False,
                )
            if _sha256(media_path) != str(job["source_sha256"]):
                raise PlatformPublishError(
                    "staged source failed SHA-256 verification",
                    code="STAGED_MEDIA_HASH_MISMATCH",
                    retryable=False,
                )
            if int(job["attempts"]) <= 1:
                self._validate_before_first_attempt(job)
            metadata = json.loads(str(job["metadata_json"]))
            if not isinstance(metadata, dict):
                raise PlatformPublishError(
                    "persisted destination metadata is invalid",
                    code="INVALID_METADATA",
                    retryable=False,
                )
            adapter_state = json.loads(str(job.get("adapter_state_json") or "{}"))
            if not isinstance(adapter_state, dict):
                adapter_state = {}
            platform = str(job["platform"])
            account_id = str(job["account_id"])
            adapter = adapter_for(platform)
            owner = str(job["lease_owner"])
            result = adapter.publish(
                media_path=media_path,
                metadata=metadata,
                account_id=account_id,
                idempotency_key=str(job["idempotency_key"]),
                remote_operation_id=str(job.get("remote_operation_id") or "") or None,
                adapter_state=adapter_state,
                on_operation=lambda op, state, active_state: self.store.record_operation(
                    int(job["id"]), owner, op, state, active_state, now=time.time()
                ),
                on_state=lambda state, active_state: self.store.save_adapter_state(
                    int(job["id"]), owner, state, active_state, now=time.time()
                ),
                on_remote_post=lambda post_id, url: self.store.save_remote_post(
                    int(job["id"]), owner, post_id, url, now=time.time()
                ),
            )
            self.store.mark_published(int(job["id"]), owner, result, now=time.time())
            self.last_success_epoch = time.time()
            self._write_heartbeat(disk_free_bytes=shutil.disk_usage(self.folder.staging_dir).free)
            return {
                "id": int(job["id"]),
                "platform": platform,
                "account_id": account_id,
                "state": "published",
                "remote_post_id": result.remote_post_id,
                "remote_url": result.remote_url,
            }
        except Exception as exc:
            try:
                final_state = self.store.mark_failure(job, str(job["lease_owner"]), exc, now=time.time())
            except Exception:
                LOG.exception("could not persist publication failure for job %s", job["id"])
                final_state = "persistence_error"
            if final_state == "permanent_failed":
                event_key = f"publication:{job['id']}:permanent_failed:{getattr(exc, 'code', type(exc).__name__)}"
                self._notify_system_failure(
                    event_key,
                    f"Publishing failed permanently for {job['platform']} account {job['account_id']}.",
                    {
                        "job_id": int(job["id"]),
                        "platform": str(job["platform"]),
                        "account_id": str(job["account_id"]),
                        "error_code": getattr(exc, "code", type(exc).__name__.upper()),
                        "error": _safe_error_message(exc),
                        "source_hash": str(job["source_sha256"]),
                    },
                )
            LOG.warning(
                "publication job %s for %s:%s ended in %s (%s)",
                job["id"],
                job["platform"],
                job["account_id"],
                final_state,
                _safe_error_message(exc),
            )
            self._write_heartbeat(status="degraded")
            return {
                "id": int(job["id"]),
                "platform": str(job["platform"]),
                "account_id": str(job["account_id"]),
                "state": final_state,
                "error_code": getattr(exc, "code", type(exc).__name__.upper()),
                "error": _safe_error_message(exc),
            }
        finally:
            stop.set()
            heartbeat.join(timeout=2)

    def _renew_lease_loop(self, job_id: int, owner: str, stop: threading.Event) -> None:
        while not stop.wait(30):
            try:
                if not self.store.renew_lease(job_id, owner, now=time.time()):
                    LOG.warning("lease lost for publication job %s", job_id)
                    return
            except sqlite3.Error:
                LOG.exception("publication lease renewal failed for job %s", job_id)

    def _notify_system_failure(self, event_key: str, message: str, details: dict[str, Any]) -> None:
        webhook = os.environ.get("AIVF_FAILURE_WEBHOOK_URL", "").strip()
        if not webhook:
            return
        try:
            parsed = urlparse(webhook)
            if parsed.scheme != "https" or not parsed.hostname:
                LOG.warning("failure notification was not sent because its webhook URL is not HTTPS")
                return
        except ValueError:
            return
        now = time.time()
        try:
            if not self.store.claim_notification(event_key, now=now):
                return
        except sqlite3.Error:
            LOG.exception("could not record notification deduplication key")
            return
        payload = {
            "text": message,
            "service": "edit-factory-v3",
            "occurred_at_epoch": now,
            "details": details,
        }
        status_code: int | None = None
        try:
            response = requests.post(webhook, json=payload, timeout=8, allow_redirects=False)
            status_code = response.status_code
            if not response.ok:
                LOG.warning("failure webhook returned HTTP %s", status_code)
        except requests.RequestException as exc:
            LOG.warning("failure webhook delivery failed (%s)", type(exc).__name__)
        try:
            self.store.set_notification_response(event_key, status_code)
        except sqlite3.Error:
            LOG.exception("could not persist failure notification result")

    def _write_heartbeat(
        self,
        *,
        disk_free_bytes: int | None = None,
        status: str = "ok",
    ) -> None:
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                pending = self.store.counts()
            except sqlite3.Error:
                pending = {}
            payload = {
                "service": "edit-factory-v3-platform-publisher",
                "updated_at_epoch": time.time(),
                "last_success_epoch": self.last_success_epoch,
                "status": status,
                "publish_enabled": self.publish_enabled,
                "queue_counts": pending,
                "disk_free_bytes": disk_free_bytes,
            }
            temporary = self.heartbeat_path.with_name(
                f".{self.heartbeat_path.name}.{uuid.uuid4().hex}.partial"
            )
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, self.heartbeat_path)
            try:
                os.chmod(self.heartbeat_path, 0o600)
            except OSError:
                pass
        except (OSError, sqlite3.Error):
            LOG.exception("failed to write publication heartbeat")

    def status(self) -> dict[str, Any]:
        jobs = self.store.jobs(limit=5000)
        by_publication: dict[str, list[str]] = {}
        for job in jobs:
            by_publication.setdefault(str(job["publication_id"]), []).append(str(job["state"]))
        aggregates: dict[str, int] = {}
        for states in by_publication.values():
            if all(state == "published" for state in states):
                aggregate = "published"
            elif any(state == "permanent_failed" for state in states) and any(state == "published" for state in states):
                aggregate = "partial_failure"
            elif all(state in FINAL_STATES for state in states) and any(state != "published" for state in states):
                aggregate = "failed"
            else:
                aggregate = "in_progress"
            aggregates[aggregate] = aggregates.get(aggregate, 0) + 1
        return {"counts_by_destination_state": self.store.counts(), "publication_outcomes": aggregates, "jobs": jobs}

    def run_once(self, *, limit: int = 100) -> dict[str, Any]:
        preparation = self.scan_and_enqueue()
        attempted = []
        if self.publish_enabled:
            for _ in range(max(1, min(int(limit), 1000))):
                result = self.process_one()
                if result is None:
                    break
                attempted.append(result)
        return {
            "preparation": preparation,
            "attempted": attempted,
            "status": self.status(),
        }

    def run_forever(self, *, poll_seconds: int = 10) -> None:
        self._write_heartbeat(status="starting")
        LOG.info(
            "platform publisher started; uploads_enabled=%s; poll_seconds=%s",
            self.publish_enabled,
            max(1, int(poll_seconds)),
        )
        while True:
            try:
                self.scan_and_enqueue()
                result = self.process_one()
                if result:
                    LOG.info(
                        "platform publication outcome: job=%s platform=%s account=%s state=%s",
                        result.get("id"),
                        result.get("platform"),
                        result.get("account_id"),
                        result.get("state"),
                    )
            except Exception as exc:
                LOG.exception("platform publisher loop encountered an error (%s)", type(exc).__name__)
                self._notify_system_failure(
                    f"publisher-loop:{type(exc).__name__}",
                    "The platform publisher loop encountered an unexpected error.",
                    {"error_type": type(exc).__name__, "error": _safe_error_message(exc)},
                )
                self._write_heartbeat(status="degraded")
            time.sleep(max(1, int(poll_seconds)))


def _folder_from_args(args: argparse.Namespace) -> FolderPublisher:
    return FolderPublisher(
        FolderPublisherConfig(
            watch_dir=Path(args.watch_dir),
            db_path=Path(args.db),
            settle_seconds=max(0, int(args.settle_seconds)),
            poll_seconds=max(1, int(args.poll_seconds)),
            max_videos_per_day=max(0, int(args.max_per_day)),
            max_file_bytes=max(1, int(args.max_file_bytes)),
            max_staged_bytes=max(1, int(args.max_staged_bytes)),
            max_duration_seconds=max(1, int(args.max_duration_seconds)),
            publish_enabled=False,
            privacy_status=str(args.privacy),
            allow_public=bool(args.allow_public),
            client_secrets_path=args.youtube_client_secrets,
            token_path=args.youtube_token_path,
        )
    )


def main(argv: Iterable[str] | None = None) -> int:
    state_dir = Path(os.environ.get("AIVF_STATE_DIR", "./state")).expanduser()
    parser = argparse.ArgumentParser(
        description="Reliably publish approved existing videos to separately tracked platform accounts"
    )
    parser.add_argument("--watch-dir", default=os.environ.get("AIVF_INCOMING_DIR", "./incoming"))
    parser.add_argument(
        "--db",
        default=os.environ.get("AIVF_FOLDER_STATE_DB", str(state_dir / "folder-publisher.sqlite")),
    )
    parser.add_argument("--settle-seconds", type=int, default=int(os.environ.get("AIVF_FOLDER_SETTLE_SECONDS", "30")))
    parser.add_argument("--poll-seconds", type=int, default=int(os.environ.get("AIVF_FOLDER_POLL_SECONDS", "10")))
    parser.add_argument("--max-per-day", type=int, default=int(os.environ.get("AIVF_FOLDER_MAX_VIDEOS_PER_DAY", "2")))
    parser.add_argument("--max-file-bytes", type=int, default=int(os.environ.get("AIVF_FOLDER_MAX_FILE_BYTES", str(8 * 1024**3))))
    parser.add_argument("--max-staged-bytes", type=int, default=int(os.environ.get("AIVF_FOLDER_MAX_STAGED_BYTES", str(20 * 1024**3))))
    parser.add_argument("--max-duration-seconds", type=int, default=int(os.environ.get("AIVF_FOLDER_MAX_DURATION_SECONDS", "7200")))
    parser.add_argument("--privacy", choices=("private", "unlisted", "public"), default=os.environ.get("AIVF_FOLDER_PRIVACY", "private"))
    parser.add_argument("--allow-public", action="store_true", default=os.environ.get("AIVF_FOLDER_ALLOW_PUBLIC", "0").strip() == "1")
    parser.add_argument("--youtube-client-secrets", default=os.environ.get("YOUTUBE_CLIENT_SECRETS"))
    parser.add_argument("--youtube-token-path", default=os.environ.get("YOUTUBE_TOKEN_PATH"))
    parser.add_argument("--max-attempts", type=int, default=int(os.environ.get("AIVF_PUBLISH_MAX_ATTEMPTS", "5")))
    parser.add_argument(
        "--min-free-disk-bytes",
        type=int,
        default=int(os.environ.get("AIVF_PUBLISH_MIN_FREE_DISK_BYTES", str(2 * 1024**3))),
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        default=os.environ.get("AIVF_MULTI_PUBLISH", "0").strip() == "1",
        help="enable real platform requests; dry-run is the default",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="scan approved source files and seed per-destination jobs without publishing")
    subparsers.add_parser("status", help="show redacted queue state and platform-level outcomes")
    once = subparsers.add_parser("run-once", help="scan and optionally attempt due platform jobs")
    once.add_argument("--limit", type=int, default=100)
    subparsers.add_parser("daemon", help="continuously scan and independently process due platform jobs")
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=os.environ.get("AIVF_LOG_LEVEL", "INFO"))

    folder = _folder_from_args(args)
    publisher = MultiPlatformPublisher(
        folder,
        publish_enabled=bool(args.publish),
        max_per_account_per_day=max(0, args.max_per_day),
        max_attempts=max(1, args.max_attempts),
        min_free_disk_bytes=max(0, args.min_free_disk_bytes),
    )
    if args.command == "scan":
        print(json.dumps(publisher.scan_and_enqueue(), indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps(publisher.status(), indent=2, sort_keys=True))
        return 0
    if args.command == "run-once":
        print(json.dumps(publisher.run_once(limit=args.limit), indent=2, sort_keys=True))
        return 0
    if args.command == "daemon":
        publisher.run_forever(poll_seconds=args.poll_seconds)
        return 0
    return 2


__all__ = [
    "Target",
    "PublicationStore",
    "MultiPlatformPublisher",
    "publication_key",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
