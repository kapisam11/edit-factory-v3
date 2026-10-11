"""Durable, per-destination publishing queue for existing local videos.

The queue persists each platform/account independently. It deliberately fails
closed when a remote result is ambiguous and the platform has no safe way to
reconcile it. Media is copied into immutable, hash-verified staging snapshots;
uploads never read a file that may still be changing.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import random
import re
import shutil
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Protocol

LOG = logging.getLogger(__name__)
SCHEMA_VERSION = 1
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
PLATFORMS = {"youtube", "instagram", "tiktok"}
STATES = {
    "queued", "processing", "ready", "uploading", "processing_remote",
    "published", "retryable_failed", "permanent_failed",
}
TERMINAL_STATES = {"published", "permanent_failed"}
ALLOWED_TRANSITIONS = {
    "queued": {"processing", "ready", "retryable_failed", "permanent_failed"},
    "processing": {"ready", "retryable_failed", "permanent_failed"},
    "ready": {"uploading", "permanent_failed"},
    "uploading": {"processing_remote", "published", "retryable_failed", "permanent_failed"},
    "processing_remote": {"processing_remote", "published", "retryable_failed", "permanent_failed"},
    "retryable_failed": {"uploading", "processing_remote", "published", "retryable_failed", "permanent_failed", "ready"},
    "published": set(),
    "permanent_failed": {"ready"},
}

class PublisherError(RuntimeError):
    """A safe, structured validation or queue error."""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

class AdapterError(RuntimeError):
    """Platform error with retry and ambiguous-outcome semantics."""
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        uncertain: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain

@dataclass(frozen=True)
class PublisherConfig:
    watch_dir: Path
    state_dir: Path
    db_path: Path
    staging_dir: Path
    settle_seconds: int = 30
    poll_seconds: int = 15
    max_file_bytes: int = 2 * 1024**3
    max_staged_bytes: int = 20 * 1024**3
    max_duration_seconds: int = 900
    max_width: int = 3840
    max_height: int = 2160
    max_fps: float = 120.0
    max_audio_channels: int = 8
    min_free_disk_bytes: int = 20 * 1024**3
    max_attempts: int = 5
    retry_base_seconds: int = 60
    retry_max_seconds: int = 3600
    lease_seconds: int = 900
    batch_size: int = 3
    publish_enabled: bool = False
    allow_public: bool = False
    decode_check: bool = True
    quality_scan: bool = True
    expected_posts_per_day: int = 1
    deadline_utc: str = ""

    @classmethod
    def from_environment(cls, watch_dir: str | Path | None = None) -> "PublisherConfig":
        def integer(name: str, default: int, minimum: int = 0) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if value < minimum:
                raise ValueError(f"{name} must be >= {minimum}")
            return value

        state_dir = Path(os.environ.get("AIVF_STATE_DIR", "./state")).expanduser().resolve()
        watched = Path(watch_dir or os.environ.get("AIVF_INCOMING_DIR", "./incoming")).expanduser().resolve()
        db_path = Path(os.environ.get("AIVF_PUBLISH_DB", str(state_dir / "multi-platform-publisher.sqlite"))).expanduser().resolve()
        staging_dir = Path(os.environ.get("AIVF_PUBLISH_STAGING_DIR", str(state_dir / "publisher-staging"))).expanduser().resolve()
        raw_fps = os.environ.get("AIVF_PUBLISH_MAX_FPS", "120")
        try:
            max_fps = float(raw_fps)
        except ValueError as exc:
            raise ValueError("AIVF_PUBLISH_MAX_FPS must be numeric") from exc
        if not math.isfinite(max_fps) or max_fps <= 0:
            raise ValueError("AIVF_PUBLISH_MAX_FPS must be finite and positive")
        deadline = os.environ.get("AIVF_PUBLISH_DEADLINE_UTC", "").strip()
        if deadline and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", deadline):
            raise ValueError("AIVF_PUBLISH_DEADLINE_UTC must use HH:MM UTC")
        config = cls(
            watch_dir=watched,
            state_dir=state_dir,
            db_path=db_path,
            staging_dir=staging_dir,
            settle_seconds=integer("AIVF_PUBLISH_SETTLE_SECONDS", 30),
            poll_seconds=integer("AIVF_PUBLISH_POLL_SECONDS", 15, 1),
            max_file_bytes=integer("AIVF_PUBLISH_MAX_FILE_BYTES", 2 * 1024**3, 1),
            max_staged_bytes=integer("AIVF_PUBLISH_MAX_STAGED_BYTES", 20 * 1024**3, 1),
            max_duration_seconds=integer("AIVF_PUBLISH_MAX_DURATION_SECONDS", 900, 1),
            max_width=integer("AIVF_PUBLISH_MAX_WIDTH", 3840, 1),
            max_height=integer("AIVF_PUBLISH_MAX_HEIGHT", 2160, 1),
            max_fps=max_fps,
            max_audio_channels=integer("AIVF_PUBLISH_MAX_AUDIO_CHANNELS", 8, 1),
            min_free_disk_bytes=integer("AIVF_PUBLISH_MIN_FREE_DISK_BYTES", 20 * 1024**3),
            max_attempts=integer("AIVF_PUBLISH_MAX_ATTEMPTS", 5, 1),
            retry_base_seconds=integer("AIVF_PUBLISH_RETRY_BASE_SECONDS", 60, 1),
            retry_max_seconds=integer("AIVF_PUBLISH_RETRY_MAX_SECONDS", 3600, 1),
            lease_seconds=integer("AIVF_PUBLISH_LEASE_SECONDS", 900, 60),
            batch_size=integer("AIVF_PUBLISH_BATCH_SIZE", 3, 1),
            publish_enabled=os.environ.get("AIVF_MULTI_PLATFORM_PUBLISH", "0").strip() == "1",
            allow_public=os.environ.get("AIVF_PUBLISH_ALLOW_PUBLIC", "0").strip() == "1",
            decode_check=os.environ.get("AIVF_PUBLISH_DECODE_CHECK", "1").strip() == "1",
            quality_scan=os.environ.get("AIVF_PUBLISH_QUALITY_SCAN", "1").strip() == "1",
            expected_posts_per_day=integer("AIVF_PUBLISH_EXPECTED_PER_DAY", 1),
            deadline_utc=deadline,
        )
        config.validate_paths()
        return config

    def validate_paths(self) -> None:
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        watch = self.watch_dir.resolve()
        for protected in (self.state_dir.resolve(), self.staging_dir.resolve(), self.db_path.resolve()):
            try:
                protected.relative_to(watch)
            except ValueError:
                continue
            raise ValueError("publisher state/database/staging must not be inside the watched video folder")
        try:
            self.staging_dir.resolve().relative_to(watch)
        except ValueError:
            pass
        else:
            raise ValueError("publisher staging must be outside the watched video folder")

class Adapter(Protocol):
    safe_retry_without_remote_id: bool
    def start(self, job: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None]) -> "AdapterUpdate": ...
    def poll(self, job: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None]) -> "AdapterUpdate": ...

@dataclass(frozen=True)
class AdapterUpdate:
    state: str
    remote_id: Optional[str] = None
    remote_url: Optional[str] = None
    provider_state: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    error: Optional[str] = None
    retryable: bool = False
    clear_remote: bool = False

def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PublisherError("INVALID_MEDIA_METADATA", f"{label} is missing or invalid") from exc
    if not math.isfinite(number):
        raise PublisherError("INVALID_MEDIA_METADATA", f"{label} is not finite")
    return number

def probe_media(path: str | Path) -> dict[str, Any]:
    """Read media metadata and reject missing or unreasonable stream properties."""
    video = Path(path)
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration,size,format_name:stream=codec_type,codec_name,width,height,avg_frame_rate,channels,sample_rate",
                "-of", "json", str(video),
            ],
            check=True, capture_output=True, text=True, timeout=30,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise PublisherError("FFPROBE_FAILED", "FFprobe could not validate this media file") from exc
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise PublisherError("INVALID_MEDIA", "FFprobe did not return a stream list")
    videos = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"]
    if not videos:
        raise PublisherError("NO_VIDEO_STREAM", "media file contains no video stream")
    first = videos[0]
    duration = _finite_number((payload.get("format") or {}).get("duration"), "duration")
    try:
        width, height = int(first.get("width")), int(first.get("height"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise PublisherError("INVALID_DIMENSIONS", "video dimensions are invalid") from exc
    fps_text = str(first.get("avg_frame_rate") or "0/0")
    try:
        numerator, denominator = fps_text.split("/", 1)
        fps = float(numerator) / float(denominator) if float(denominator) else 0.0
    except (ValueError, ZeroDivisionError, OverflowError):
        fps = 0.0
    if duration <= 0:
        raise PublisherError("INVALID_DURATION", "video duration must be positive")
    if width < 1 or height < 1 or width > 32768 or height > 32768:
        raise PublisherError("INVALID_DIMENSIONS", "video dimensions are invalid")
    size = video.stat().st_size
    return {
        "duration_seconds": duration,
        "size_bytes": size,
        "width": width,
        "height": height,
        "fps": fps,
        "video_codec": str(first.get("codec_name") or "").lower(),
        "audio_codec": str(audios[0].get("codec_name") or "").lower() if audios else "",
        "audio_streams": len(audios),
        "audio_channels": max((int(audio.get("channels") or 0) for audio in audios), default=0),
        "format_name": str((payload.get("format") or {}).get("format_name") or ""),
        "extension": video.suffix.lower(),
    }

def decode_check(path: str | Path, duration_seconds: float) -> None:
    timeout = max(60.0, min(float(duration_seconds) * 2.0, 1800.0))
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublisherError("FFMPEG_DECODE_FAILED", "FFmpeg could not fully decode the media file") from exc
    if result.returncode != 0:
        raise PublisherError("CORRUPT_MEDIA", "media decode failed; refusing to publish a possibly corrupt file")

def quality_scan(path: str | Path, media: dict[str, Any]) -> list[str]:
    """Report suspicious black/silent sections without rejecting intentional edits."""
    warnings: list[str] = []
    try:
        black = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf", "blackdetect=d=3:pix_th=0.10", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=max(60.0, min(float(media["duration_seconds"]) * 2.0, 1800.0)),
        )
        if black.returncode == 0 and re.search(r"black_duration:([0-9.]+)", black.stderr):
            durations = [float(value) for value in re.findall(r"black_duration:([0-9.]+)", black.stderr)]
            if sum(durations) >= max(5.0, float(media["duration_seconds"]) * 0.5):
                warnings.append("EXCESSIVE_BLACK_FRAMES")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        warnings.append("BLACK_FRAME_SCAN_UNAVAILABLE")
    if int(media.get("audio_streams") or 0) > 0:
        try:
            silent = subprocess.run(
                ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "silencedetect=n=-50dB:d=5", "-f", "null", "-"],
                capture_output=True, text=True, timeout=max(60.0, min(float(media["duration_seconds"]) * 2.0, 1800.0)),
            )
            if silent.returncode == 0 and "silence_start:" in silent.stderr:
                intervals = []
                for start, end in re.findall(r"silence_start:\s*([0-9.]+).*?silence_end:\s*([0-9.]+)", silent.stderr, re.S):
                    intervals.append(max(0.0, float(end) - float(start)))
                if sum(intervals) >= max(10.0, float(media["duration_seconds"]) * 0.6):
                    warnings.append("EXCESSIVE_SILENCE")
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            warnings.append("AUDIO_SCAN_UNAVAILABLE")
    else:
        warnings.append("NO_AUDIO_STREAM")
    return warnings

def parse_destinations(value: str) -> list[dict[str, str]]:
    destinations: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError("destinations must use platform:account_id syntax")
        platform, account = (part.strip() for part in item.split(":", 1))
        platform = platform.lower()
        if platform not in PLATFORMS or not account or len(account) > 256:
            raise ValueError(f"invalid publishing destination: {item}")
        key = (platform, account)
        if key in seen:
            raise ValueError(f"duplicate publishing destination: {item}")
        seen.add(key)
        destinations.append({"platform": platform, "account_id": account})
    return destinations

class PublisherStore:
    """SQLite transaction boundary for source observations, jobs, leases and events."""
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"publisher database schema {version} is newer than supported schema {SCHEMA_VERSION}")
            if version == SCHEMA_VERSION:
                return
            conn.executescript(
                """
                CREATE TABLE sources (
                    path TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL UNIQUE,
                    observed_size INTEGER NOT NULL,
                    observed_mtime_ns INTEGER NOT NULL,
                    stable_since REAL NOT NULL,
                    state TEXT NOT NULL,
                    source_sha256 TEXT,
                    staged_path TEXT,
                    last_error_code TEXT,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE publish_jobs (
                    id TEXT PRIMARY KEY,
                    publication_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    staged_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    remote_id TEXT,
                    remote_url TEXT,
                    provider_state_json TEXT NOT NULL DEFAULT '{}',
                    last_error_code TEXT,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    published_at REAL,
                    UNIQUE(publication_id, platform, account_id)
                );
                CREATE INDEX publish_jobs_due_upload ON publish_jobs(state, next_attempt_at, lease_expires_at);
                CREATE INDEX publish_jobs_due_remote ON publish_jobs(remote_id, state, next_attempt_at, lease_expires_at);
                CREATE INDEX publish_jobs_source ON publish_jobs(source_id, created_at);
                CREATE TABLE publish_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT,
                    event TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    at REAL NOT NULL
                );
                CREATE INDEX publish_events_job ON publish_events(job_id, seq);
                CREATE TABLE publish_alerts (
                    alert_key TEXT PRIMARY KEY,
                    created_at REAL NOT NULL
                );
                PRAGMA user_version=1;
                """
            )

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        try:
            value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
        except (TypeError, ValueError):
            value["metadata"] = {}
        try:
            value["provider_state"] = json.loads(value.pop("provider_state_json") or "{}")
        except (TypeError, ValueError):
            value["provider_state"] = {}
        return value

    def observe_source(self, path: str, size: int, mtime_ns: int, now: float) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM sources WHERE path=?", (path,)).fetchone()
            if row is None:
                source_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO sources(path,source_id,observed_size,observed_mtime_ns,stable_since,state,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?)",
                    (path, source_id, size, mtime_ns, now, now, now),
                )
                return {"source_id": source_id, "stable_since": now, "changed": True}
            changed = int(row["observed_size"]) != size or int(row["observed_mtime_ns"]) != mtime_ns
            stable_since = now if changed else float(row["stable_since"])
            conn.execute(
                "UPDATE sources SET observed_size=?,observed_mtime_ns=?,stable_since=?,state=?,source_sha256=CASE WHEN ? THEN NULL ELSE source_sha256 END,staged_path=CASE WHEN ? THEN NULL ELSE staged_path END,last_error_code=NULL,last_error=NULL,updated_at=? WHERE path=?",
                (size, mtime_ns, stable_since, "queued" if changed else str(row["state"]), int(changed), int(changed), now, path),
            )
            return {"source_id": str(row["source_id"]), "stable_since": stable_since, "changed": changed}

    def mark_source(self, path: str, *, state: str, digest: Optional[str] = None, staged_path: Optional[str] = None, error_code: Optional[str] = None, error: Optional[str] = None, now: Optional[float] = None) -> None:
        if state not in STATES and state not in {"waiting_stable", "needs_review", "policy_review", "invalid", "staged"}:
            raise ValueError(f"invalid source state: {state}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE sources SET state=?,source_sha256=COALESCE(?,source_sha256),staged_path=COALESCE(?,staged_path),last_error_code=?,last_error=?,updated_at=? WHERE path=?",
                (state, digest, staged_path, error_code, error[:500] if error else None, time.time() if now is None else now, path),
            )

    def enqueue_job(self, *, publication_id: str, source_id: str, source_path: str, staged_path: str, digest: str, platform: str, account_id: str, metadata: dict[str, Any], max_attempts: int, now: float) -> bool:
        job_id = uuid.uuid4().hex
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """INSERT OR IGNORE INTO publish_jobs
                (id,publication_id,source_id,source_path,staged_path,source_sha256,platform,account_id,metadata_json,state,max_attempts,next_attempt_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'ready',?,?,?,?)""",
                (job_id, publication_id, source_id, source_path, staged_path, digest, platform, account_id, json.dumps(metadata, sort_keys=True), max_attempts, now, now, now),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                self._event(conn, job_id, "job_ready", {"platform": platform, "account_id": account_id})
            return inserted

    @staticmethod
    def _event(conn: sqlite3.Connection, job_id: Optional[str], event: str, details: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO publish_events(job_id,event,details_json,at) VALUES(?,?,?,?)",
            (job_id, event, json.dumps(details, sort_keys=True, ensure_ascii=True)[:4000], time.time()),
        )

    def recover_expired_leases(self, *, now: Optional[float] = None) -> dict[str, int]:
        instant = time.time() if now is None else now
        result = {"remote_resume": 0, "youtube_safe_retry": 0, "uncertain_blocked": 0}
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id,platform,remote_id FROM publish_jobs WHERE state='uploading' AND lease_expires_at IS NOT NULL AND lease_expires_at<?",
                (instant,),
            ).fetchall()
            for row in rows:
                job_id, platform, remote_id = str(row["id"]), str(row["platform"]), row["remote_id"]
                if remote_id:
                    state, code, error = "processing_remote", None, None
                    result["remote_resume"] += 1
                elif platform == "youtube":
                    # youtube_publisher maintains its own durable idempotency record
                    # and reconciles the channel before performing a duplicate upload.
                    state, code, error = "retryable_failed", "LEASE_EXPIRED_RECONCILE", "worker stopped; YouTube idempotency reconciliation is required"
                    result["youtube_safe_retry"] += 1
                else:
                    # Meta/TikTok do not expose reliable lookup by our local key.
                    # Unknown result without a persisted platform operation ID must
                    # never trigger a blind second POST.
                    state, code, error = "permanent_failed", "UPLOAD_OUTCOME_UNKNOWN", "worker stopped before a remote operation ID was saved; verify the destination account before any manual recovery"
                    result["uncertain_blocked"] += 1
                conn.execute(
                    "UPDATE publish_jobs SET state=?,lease_owner=NULL,lease_expires_at=NULL,next_attempt_at=?,last_error_code=?,last_error=?,updated_at=? WHERE id=?",
                    (state, instant, code, error, instant, job_id),
                )
                self._event(conn, job_id, "lease_recovered", {"state": state, "error_code": code or ""})
        return result

    def _claim(self, *, owner: str, now: float, lease_seconds: int, remote: bool) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if remote:
                query = """SELECT * FROM publish_jobs WHERE remote_id IS NOT NULL
                    AND state IN ('processing_remote','retryable_failed')
                    AND next_attempt_at<=? AND (lease_owner IS NULL OR lease_expires_at<?)
                    ORDER BY next_attempt_at,created_at LIMIT 1"""
            else:
                query = """SELECT * FROM publish_jobs WHERE remote_id IS NULL
                    AND state IN ('ready','retryable_failed')
                    AND next_attempt_at<=? AND (lease_owner IS NULL OR lease_expires_at<?)
                    ORDER BY next_attempt_at,created_at LIMIT 1"""
            row = conn.execute(query, (now, now)).fetchone()
            if row is None:
                return None
            job_id = str(row["id"])
            if remote:
                conn.execute(
                    "UPDATE publish_jobs SET lease_owner=?,lease_expires_at=?,updated_at=? WHERE id=?",
                    (owner, now + lease_seconds, now, job_id),
                )
                self._event(conn, job_id, "remote_poll_claimed", {"owner": owner[:8]})
            else:
                conn.execute(
                    "UPDATE publish_jobs SET state='uploading',attempts=attempts+1,lease_owner=?,lease_expires_at=?,updated_at=? WHERE id=?",
                    (owner, now + lease_seconds, now, job_id),
                )
                self._event(conn, job_id, "upload_claimed", {"owner": owner[:8], "attempt": int(row["attempts"]) + 1})
            claimed = conn.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
            return self._job(claimed) if claimed else None

    def claim_upload(self, owner: str, now: float, lease_seconds: int) -> Optional[dict[str, Any]]:
        return self._claim(owner=owner, now=now, lease_seconds=lease_seconds, remote=False)

    def claim_remote(self, owner: str, now: float, lease_seconds: int) -> Optional[dict[str, Any]]:
        return self._claim(owner=owner, now=now, lease_seconds=lease_seconds, remote=True)

    def save_checkpoint(self, job_id: str, owner: str, remote_id: Optional[str], provider_state: dict[str, Any]) -> None:
        encoded = json.dumps(provider_state, sort_keys=True)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE publish_jobs SET remote_id=COALESCE(?,remote_id),provider_state_json=?,updated_at=? WHERE id=? AND lease_owner=?",
                (remote_id, encoded, time.time(), job_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("publishing lease was lost; refusing an unfenced checkpoint")
            self._event(conn, job_id, "remote_checkpoint", {"has_remote_id": bool(remote_id), "phase": str(provider_state.get("phase") or "")[:80]})

    def finish_claim(self, job: dict[str, Any], owner: str, update: AdapterUpdate, *, next_attempt_at: float = 0.0) -> None:
        if update.state not in STATES:
            raise ValueError(f"adapter returned invalid state: {update.state}")
        job_id = str(job["id"])
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT state,remote_id,remote_url,provider_state_json FROM publish_jobs WHERE id=? AND lease_owner=?", (job_id, owner)).fetchone()
            if current is None:
                raise RuntimeError("publishing lease was lost; refusing an unfenced completion")
            old_state = str(current["state"])
            if update.state != old_state and update.state not in ALLOWED_TRANSITIONS.get(old_state, set()):
                raise RuntimeError(f"invalid publisher state transition: {old_state} -> {update.state}")
            try:
                state_json = json.dumps(update.provider_state if update.provider_state is not None else json.loads(current["provider_state_json"] or "{}"), sort_keys=True)
            except (TypeError, ValueError):
                state_json = "{}"
            if update.clear_remote:
                remote_id, remote_url, state_json = None, None, "{}"
            else:
                remote_id = update.remote_id or current["remote_id"]
                remote_url = update.remote_url or current["remote_url"]
            published_at = now if update.state == "published" else None
            error_code = None if update.state in {"published", "processing_remote"} else update.error_code
            error = None if update.state == "published" else (update.error[:500] if update.error else None)
            cursor = conn.execute(
                """UPDATE publish_jobs SET state=?,remote_id=?,remote_url=?,provider_state_json=?,
                last_error_code=?,last_error=?,next_attempt_at=?,lease_owner=NULL,lease_expires_at=NULL,
                published_at=COALESCE(?,published_at),updated_at=? WHERE id=? AND lease_owner=?""",
                (update.state, remote_id, remote_url, state_json, error_code, error, next_attempt_at, published_at, now, job_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("publishing lease was lost during completion")
            self._event(conn, job_id, "state_transition", {
                "from": old_state, "to": update.state,
                "remote_id_saved": bool(remote_id), "error_code": error_code or "",
            })

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
            return self._job(row) if row else None

    def retry_job(self, job_id: str, *, now: Optional[float] = None) -> None:
        instant = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state,remote_id,last_error_code FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["remote_id"]:
                raise ValueError("this job has a remote operation ID; reconcile the platform status instead of forcing another upload")
            if row["last_error_code"] == "UPLOAD_OUTCOME_UNKNOWN":
                raise ValueError("this job has an ambiguous remote outcome; verify the account manually before retrying")
            if row["state"] not in {"retryable_failed", "permanent_failed"}:
                raise ValueError("only failed jobs without a remote operation ID can be manually retried")
            conn.execute(
                "UPDATE publish_jobs SET state='ready',attempts=0,next_attempt_at=?,last_error_code=NULL,last_error=NULL,updated_at=? WHERE id=?",
                (instant, instant, job_id),
            )
            self._event(conn, job_id, "manual_retry_requested", {})

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,publication_id,source_path,source_sha256,platform,account_id,state,attempts,max_attempts,next_attempt_at,remote_id,remote_url,last_error_code,last_error,created_at,updated_at,published_at FROM publish_jobs ORDER BY created_at DESC LIMIT ?",
                (bounded,),
            ).fetchall()
            return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT state,COUNT(*) AS total FROM publish_jobs GROUP BY state").fetchall()
            result = {state: 0 for state in sorted(STATES)}
            result.update({str(row["state"]): int(row["total"]) for row in rows})
            return result

    def events(self, job_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event,details_json,at FROM publish_events WHERE job_id=? ORDER BY seq DESC LIMIT ?",
                (job_id, max(1, min(int(limit), 200))),
            ).fetchall()
            result = []
            for row in rows:
                try:
                    details = json.loads(row["details_json"] or "{}")
                except ValueError:
                    details = {}
                result.append({"event": row["event"], "details": details, "at": row["at"]})
            return result

    def published_today(self, *, now: Optional[float] = None) -> int:
        instant = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
        start = instant.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM publish_jobs WHERE state='published' AND published_at>=?", (start,)).fetchone()
            return int(row[0])

    def alert_claim(self, key: str, *, now: Optional[float] = None) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("INSERT OR IGNORE INTO publish_alerts(alert_key,created_at) VALUES(?,?)", (key, time.time() if now is None else now))
            return cursor.rowcount == 1

    def alert_release(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM publish_alerts WHERE alert_key=?", (key,))

    def source_summary(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT state,COUNT(*) AS total FROM sources GROUP BY state").fetchall()
            return {str(row["state"]): int(row["total"]) for row in rows}

def configured_platforms() -> dict[str, bool]:
    """Return configuration presence only; never return tokens or token paths."""
    youtube = bool(os.environ.get("YOUTUBE_CLIENT_SECRETS") and os.environ.get("YOUTUBE_TOKEN_PATH"))
    instagram = bool(os.environ.get("INSTAGRAM_ACCESS_TOKEN") and os.environ.get("INSTAGRAM_GRAPH_API_VERSION"))
    tiktok = bool(os.environ.get("TIKTOK_ACCESS_TOKEN"))
    return {"youtube": youtube, "instagram": instagram, "tiktok": tiktok}

class PublisherService:
    def __init__(self, config: PublisherConfig, adapters: Optional[dict[str, Adapter]] = None) -> None:
        config.validate_paths()
        self.config = config
        self.store = PublisherStore(config.db_path)
        if adapters is None:
            from .platform_publishers import build_adapters
            adapters = build_adapters()
        self.adapters = adapters
        self.worker_id = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
        self.store.recover_expired_leases()

    def _relative_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.config.watch_dir.resolve()).as_posix()

    def _candidates(self) -> Iterator[Path]:
        root = self.config.watch_dir.resolve()
        for path in root.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
                if resolved == self.config.db_path or resolved == self.config.staging_dir:
                    continue
                try:
                    resolved.relative_to(self.config.staging_dir.resolve())
                except ValueError:
                    yield resolved
            except (OSError, ValueError):
                continue

    def validate_media(self, path: Path) -> dict[str, Any]:
        media = probe_media(path)
        if int(media["size_bytes"]) > self.config.max_file_bytes:
            raise PublisherError("FILE_TOO_LARGE", "file exceeds the configured publisher input byte limit")
        if float(media["duration_seconds"]) > self.config.max_duration_seconds:
            raise PublisherError("DURATION_LIMIT", "video duration exceeds the configured publisher limit")
        if int(media["width"]) > self.config.max_width or int(media["height"]) > self.config.max_height:
            raise PublisherError("RESOLUTION_LIMIT", "video dimensions exceed the configured publisher limit")
        if float(media["fps"]) > self.config.max_fps:
            raise PublisherError("FRAME_RATE_LIMIT", "video frame rate exceeds the configured publisher limit")
        if int(media["audio_channels"]) > self.config.max_audio_channels:
            raise PublisherError("AUDIO_CHANNEL_LIMIT", "video audio channel count exceeds the configured limit")
        if self.config.decode_check:
            decode_check(path, float(media["duration_seconds"]))
        warnings = quality_scan(path, media) if self.config.quality_scan else []
        media["quality_warnings"] = warnings
        return media

    @staticmethod
    def _load_sidecar(video: Path) -> dict[str, Any]:
        sidecar = Path(str(video) + ".publish.json")
        if sidecar.is_symlink():
            raise PublisherError("UNSAFE_SIDECAR", "publish sidecar must not be a symbolic link")
        if not sidecar.is_file():
            raise PublisherError("SIDECAR_MISSING", "publish sidecar missing; inspect the video and explicitly approve it")
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PublisherError("SIDECAR_INVALID", "publish sidecar is not valid readable JSON") from exc
        if not isinstance(payload, dict):
            raise PublisherError("SIDECAR_INVALID", "publish sidecar must contain a JSON object")
        return payload

    @staticmethod
    def _validate_approval(payload: dict[str, Any], digest: str) -> list[dict[str, str]]:
        if payload.get("publish_approved") is not True or payload.get("rights_reviewed") is not True:
            raise PublisherError("APPROVAL_REQUIRED", "explicit publishing approval and rights review are required")
        if payload.get("approved_sha256") != digest:
            raise PublisherError("APPROVAL_STALE", "source bytes changed since approval; review and approve the file again")
        if not isinstance(payload.get("ai_generated"), bool) or not isinstance(payload.get("realistic_alteration"), bool):
            raise PublisherError("DISCLOSURE_UNDECLARED", "AI-generated and realistic-alteration flags must both be explicitly declared")
        if (payload["ai_generated"] or payload["realistic_alteration"]) and payload.get("disclosure_reviewed") is not True:
            raise PublisherError("DISCLOSURE_REVIEW_REQUIRED", "review and acknowledge the required AI/altered-media disclosure before publishing")
        publication_id = str(payload.get("publication_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", publication_id):
            raise PublisherError("PUBLICATION_ID_INVALID", "sidecar publication_id is missing or invalid")
        raw_destinations = payload.get("destinations")
        if not isinstance(raw_destinations, list) or not raw_destinations:
            raise PublisherError("DESTINATIONS_REQUIRED", "declare one or more explicit destinations in the publish sidecar")
        destinations: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in raw_destinations:
            if not isinstance(item, dict):
                raise PublisherError("DESTINATION_INVALID", "each destination must be an object")
            platform = str(item.get("platform") or "").strip().lower()
            account_id = str(item.get("account_id") or "").strip()
            if platform not in PLATFORMS or not account_id or len(account_id) > 256:
                raise PublisherError("DESTINATION_INVALID", "destination platform/account_id is invalid")
            key = (platform, account_id)
            if key in seen:
                raise PublisherError("DESTINATION_DUPLICATE", "duplicate platform/account destination in sidecar")
            seen.add(key)
            destinations.append({"platform": platform, "account_id": account_id})
        return destinations

    def _stage(self, source: Path, digest: str) -> Path:
        target = self.config.staging_dir / digest[:2] / f"{digest}{source.suffix.lower()}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256_file(target) != digest:
                raise PublisherError("STAGED_SNAPSHOT_CORRUPT", "existing content-addressed snapshot failed SHA-256 verification")
            return target
        staged_bytes = sum(p.stat().st_size for p in self.config.staging_dir.rglob("*") if p.is_file() and not p.name.endswith(".partial"))
        size = source.stat().st_size
        if staged_bytes + size > self.config.max_staged_bytes:
            raise PublisherError("STAGING_QUOTA", "publisher staging quota would be exceeded")
        if shutil.disk_usage(self.config.state_dir).free < self.config.min_free_disk_bytes + size:
            raise PublisherError("LOW_DISK_SPACE", "insufficient free disk space to reserve the staged media snapshot")
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
        before = source.stat()
        if source.is_symlink():
            raise PublisherError("UNSAFE_SOURCE", "source became a symbolic link during ingestion")
        try:
            with source.open("rb") as src, temporary.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            after = source.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise PublisherError("SOURCE_CHANGED", "source changed while staging; wait for it to stabilize and approve it again")
            if sha256_file(temporary) != digest or temporary.stat().st_size != size:
                raise PublisherError("STAGING_HASH_MISMATCH", "staged snapshot did not match the approved source hash")
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return target

    @staticmethod
    def _platform_media_check(platform: str, path: Path, media: dict[str, Any]) -> None:
        if platform == "instagram":
            if path.suffix.lower() not in {".mp4", ".mov"}:
                raise PublisherError("UNSUPPORTED_MEDIA_FORMAT", "Instagram Reels accepts MP4 or MOV media, not this file extension")
            if int(media["size_bytes"]) > 300 * 1024**2:
                raise PublisherError("PLATFORM_FILE_SIZE_LIMIT", "Instagram Reels file exceeds the configured 300 MiB safety limit")
            if not (23 <= float(media["fps"]) <= 60):
                raise PublisherError("PLATFORM_FRAME_RATE_LIMIT", "Instagram Reels frame rate must be between 23 and 60 FPS")
            if media["video_codec"] not in {"h264", "hevc"}:
                raise PublisherError("UNSUPPORTED_VIDEO_CODEC", "Instagram Reels requires a supported H.264/HEVC video stream")
        if platform == "tiktok":
            if path.suffix.lower() != ".mp4" or media["video_codec"] != "h264":
                raise PublisherError("UNSUPPORTED_MEDIA_FORMAT", "TikTok direct posting requires an MP4 with H.264 video in this publisher")
        # No automatic re-encode is performed; unsupported input is rejected rather
        # than silently spending CPU or altering the source quality.

    def scan(self) -> dict[str, Any]:
        now = time.time()
        result: dict[str, Any] = {
            "observed_files": 0, "waiting_stable": 0, "needs_review": 0,
            "ready_jobs_added": 0, "duplicates_ignored": 0, "blocked": 0, "errors": 0,
        }
        for video in self._candidates():
            result["observed_files"] += 1
            relative = self._relative_path(video)
            try:
                stat_result = video.stat()
                observed = self.store.observe_source(relative, stat_result.st_size, stat_result.st_mtime_ns, now)
                if now - float(observed["stable_since"]) < self.config.settle_seconds:
                    self.store.mark_source(relative, state="waiting_stable", now=now)
                    result["waiting_stable"] += 1
                    continue
                payload = self._load_sidecar(video)
                digest = sha256_file(video)
                destinations = self._validate_approval(payload, digest)
                media = self.validate_media(video)
                publication_id = str(payload["publication_id"])
                privacy = str(payload.get("privacy_status") or "private").strip().lower()
                if privacy not in {"private", "unlisted", "public"}:
                    raise PublisherError("PRIVACY_INVALID", "privacy_status must be private, unlisted or public")
                if privacy == "public" and not self.config.allow_public:
                    raise PublisherError("PUBLIC_PUBLISH_BLOCKED", "public publishing is disabled; explicitly opt in through AIVF_PUBLISH_ALLOW_PUBLIC=1")
                title = str(payload.get("title") or video.stem).strip()
                description = str(payload.get("description") or "")
                tags = payload.get("tags") or []
                if not title or len(title) > 100 or len(description) > 5000 or not isinstance(tags, list) or len(tags) > 30 or any(not isinstance(tag, str) or len(tag) > 100 for tag in tags):
                    raise PublisherError("METADATA_INVALID", "title, description or tags exceed publishing metadata limits")
                for destination in destinations:
                    self._platform_media_check(destination["platform"], video, media)
                staged = self._stage(video, digest)
                self.store.mark_source(relative, state="staged", digest=digest, staged_path=str(staged), now=now)
                source_row = self._source_id(relative)
                source_id = str(source_row["source_id"])
                for destination in destinations:
                    job_metadata = {
                        "title": title,
                        "description": description,
                        "tags": tags,
                        "privacy_status": privacy,
                        "category_id": str(payload.get("category_id") or "22"),
                        "ai_generated": payload["ai_generated"],
                        "realistic_alteration": payload["realistic_alteration"],
                        "disclosure_reviewed": payload.get("disclosure_reviewed") is True,
                        "media": media,
                        "options": payload.get("options") if isinstance(payload.get("options"), dict) else {},
                    }
                    added = self.store.enqueue_job(
                        publication_id=publication_id,
                        source_id=source_id,
                        source_path=relative,
                        staged_path=str(staged),
                        digest=digest,
                        platform=destination["platform"],
                        account_id=destination["account_id"],
                        metadata=job_metadata,
                        max_attempts=self.config.max_attempts,
                        now=now,
                    )
                    result["ready_jobs_added" if added else "duplicates_ignored"] += 1
                self.store.mark_source(relative, state="ready", digest=digest, staged_path=str(staged), now=now)
            except PublisherError as exc:
                state = "needs_review" if exc.code in {
                    "SIDECAR_MISSING", "SIDECAR_INVALID", "APPROVAL_REQUIRED",
                    "APPROVAL_STALE", "DISCLOSURE_UNDECLARED",
                    "DISCLOSURE_REVIEW_REQUIRED", "DESTINATIONS_REQUIRED",
                } else "policy_review" if exc.code == "PUBLIC_PUBLISH_BLOCKED" else "invalid"
                self.store.mark_source(relative, state=state, error_code=exc.code, error=str(exc), now=now)
                result["needs_review" if state == "needs_review" else "blocked"] += 1
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.store.mark_source(relative, state="invalid", error_code="INGESTION_ERROR", error=f"ingestion failed ({type(exc).__name__})", now=now)
                result["errors"] += 1
                LOG.warning("publisher ingestion failed for %s (%s)", relative, type(exc).__name__)
        result["queue_counts"] = self.store.counts()
        return result

    def _source_id(self, relative: str) -> dict[str, Any]:
        # The scan has already observed this path. The stable source identity is
        # stored in SQLite rather than being inferred from the file hash.
        with self.store._connect() as conn:
            row = conn.execute("SELECT source_id FROM sources WHERE path=?", (relative,)).fetchone()
            if row is None:
                raise RuntimeError("source identity disappeared during ingestion")
            return {"source_id": str(row["source_id"])}

    def _retry_delay(self, attempt: int) -> float:
        base = min(self.config.retry_max_seconds, self.config.retry_base_seconds * (2 ** max(0, attempt - 1)))
        return random.uniform(base * 0.75, base * 1.25)

    def _notify(self, key: str, payload: dict[str, Any]) -> None:
        url = os.environ.get("AIVF_FAILURE_WEBHOOK_URL", "").strip()
        if not url:
            return
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            LOG.warning("publishing webhook is not HTTPS; alert skipped")
            return
        if not self.store.alert_claim(key):
            return
        try:
            import requests
            response = requests.post(url, json=payload, timeout=(3, 5), allow_redirects=False)
            if response.status_code >= 300:
                self.store.alert_release(key)
        except Exception:
            self.store.alert_release(key)
            LOG.warning("publishing alert delivery failed (%s)", key[:80])

    def _finish_error(self, job: dict[str, Any], owner: str, exc: Exception, *, polling: bool) -> None:
        current = self.store.get_job(str(job["id"])) or job
        adapter = self.adapters.get(str(job["platform"]))
        error = exc if isinstance(exc, AdapterError) else AdapterError(
            "UNEXPECTED_ADAPTER_ERROR",
            f"platform adapter raised {type(exc).__name__}; inspect local diagnostics without exposing tokens",
            retryable=False,
        )
        remote_id = current.get("remote_id")
        now = time.time()
        if polling and remote_id and error.retryable and error.code not in {"TOKEN_EXPIRED", "PERMISSION_DENIED", "ACCOUNT_MISMATCH"}:
            update = AdapterUpdate("retryable_failed", str(remote_id), current.get("remote_url"), current.get("provider_state"), error.code, str(error), retryable=True)
            next_at = now + self._retry_delay(max(1, int(current.get("attempts") or 1)))
        elif not polling and remote_id:
            update = AdapterUpdate("processing_remote", str(remote_id), current.get("remote_url"), current.get("provider_state"), error.code, str(error), retryable=True)
            next_at = now + self.config.poll_seconds
        elif error.uncertain and not bool(getattr(adapter, "safe_retry_without_remote_id", False)):
            update = AdapterUpdate("permanent_failed", None, None, {}, "UPLOAD_OUTCOME_UNKNOWN", "the remote operation may have succeeded but returned no operation ID; verify the platform account manually before any retry")
            next_at = 0
        elif error.retryable and int(current.get("attempts") or 0) < int(current.get("max_attempts") or self.config.max_attempts):
            update = AdapterUpdate("retryable_failed", None, None, current.get("provider_state"), error.code, str(error), retryable=True)
            next_at = now + self._retry_delay(max(1, int(current.get("attempts") or 1)))
        else:
            update = AdapterUpdate("permanent_failed", None, None, current.get("provider_state"), error.code, str(error))
            next_at = 0
        self.store.finish_claim(current, owner, update, next_attempt_at=next_at)
        LOG.warning("publish job %s on %s transitioned to %s (%s)", str(job["id"])[:12], job["platform"], update.state, update.error_code or "")
        if update.state == "permanent_failed" or int(current.get("attempts") or 0) >= int(current.get("max_attempts") or self.config.max_attempts):
            self._notify(f"job:{job['id']}:{update.state}:{update.error_code}", {
                "event": "publish_failed", "job_id": str(job["id"]), "platform": str(job["platform"]),
                "account_id": str(job["account_id"]), "state": update.state,
                "error_code": update.error_code, "message": update.error,
            })

    def process_upload_one(self) -> bool:
        owner = uuid.uuid4().hex
        job = self.store.claim_upload(owner, time.time(), self.config.lease_seconds)
        if job is None:
            return False
        adapter = self.adapters.get(str(job["platform"]))
        if adapter is None:
            self.store.finish_claim(job, owner, AdapterUpdate("permanent_failed", error_code="PLATFORM_NOT_CONFIGURED", error="no adapter is configured for this destination"))
            return True
        staged = Path(str(job["staged_path"]))
        if not staged.is_file() or staged.is_symlink() or sha256_file(staged) != str(job["source_sha256"]):
            self.store.finish_claim(job, owner, AdapterUpdate("permanent_failed", error_code="STAGED_SNAPSHOT_INVALID", error="immutable staged media is missing or its SHA-256 hash no longer matches"))
            self._notify(f"job:{job['id']}:STAGED_SNAPSHOT_INVALID", {"event": "publish_failed", "job_id": str(job["id"]), "platform": job["platform"], "state": "permanent_failed", "error_code": "STAGED_SNAPSHOT_INVALID"})
            return True
        def checkpoint(remote_id: Optional[str], provider_state: dict[str, Any]) -> None:
            self.store.save_checkpoint(str(job["id"]), owner, remote_id, provider_state)
            fresh = self.store.get_job(str(job["id"]))
            if fresh:
                job.update(fresh)
        try:
            update = adapter.start(job, checkpoint)
            self.store.finish_claim(job, owner, update, next_attempt_at=(time.time() + self.config.poll_seconds if update.state == "processing_remote" else 0))
        except Exception as exc:
            self._finish_error(job, owner, exc, polling=False)
        return True

    def process_remote_one(self) -> bool:
        owner = uuid.uuid4().hex
        job = self.store.claim_remote(owner, time.time(), self.config.lease_seconds)
        if job is None:
            return False
        adapter = self.adapters.get(str(job["platform"]))
        if adapter is None:
            self.store.finish_claim(job, owner, AdapterUpdate("permanent_failed", error_code="PLATFORM_NOT_CONFIGURED", error="no adapter is configured for this destination"))
            return True
        def checkpoint(remote_id: Optional[str], provider_state: dict[str, Any]) -> None:
            self.store.save_checkpoint(str(job["id"]), owner, remote_id, provider_state)
            fresh = self.store.get_job(str(job["id"]))
            if fresh:
                job.update(fresh)
        try:
            update = adapter.poll(job, checkpoint)
            if update.state == "processing_remote":
                next_at = time.time() + self.config.poll_seconds
            elif update.state == "retryable_failed" and not update.clear_remote:
                next_at = time.time() + self._retry_delay(max(1, int(job.get("attempts") or 1)))
            else:
                next_at = 0
            self.store.finish_claim(job, owner, update, next_attempt_at=next_at)
            if update.state == "permanent_failed":
                self._notify(f"job:{job['id']}:{update.state}:{update.error_code}", {
                    "event": "publish_failed", "job_id": str(job["id"]), "platform": job["platform"],
                    "account_id": job["account_id"], "state": update.state,
                    "error_code": update.error_code, "message": update.error,
                })
        except Exception as exc:
            self._finish_error(job, owner, exc, polling=True)
        return True

    def check_deadline(self) -> None:
        deadline = self.config.deadline_utc
        if not deadline:
            return
        now = datetime.now(timezone.utc)
        if now.strftime("%H:%M") < deadline:
            return
        published = self.store.published_today()
        if published >= self.config.expected_posts_per_day:
            return
        day = now.strftime("%Y-%m-%d")
        self._notify(f"missed-deadline:{day}", {
            "event": "publishing_deadline_missed", "date_utc": day,
            "deadline_utc": deadline, "published_today": published,
            "expected_posts_per_day": self.config.expected_posts_per_day,
        })

    def run_once(self) -> dict[str, Any]:
        scan = self.scan()
        polled = 0
        for _ in range(self.config.batch_size):
            if not self.process_remote_one():
                break
            polled += 1
        uploads = 0
        if self.config.publish_enabled:
            for _ in range(self.config.batch_size):
                if not self.process_upload_one():
                    break
                uploads += 1
        self.check_deadline()
        return {
            "scan": scan,
            "remote_jobs_processed": polled,
            "upload_jobs_processed": uploads,
            "queue_counts": self.store.counts(),
            "source_counts": self.store.source_summary(),
        }

    def run_forever(self) -> None:
        from .publisher_lock import PublisherProcessLock
        with PublisherProcessLock(self.config.state_dir / "multi-platform-publisher.lock"):
            LOG.info("multi-platform publisher started; publish_enabled=%s; watched folder=%s", self.config.publish_enabled, self.config.watch_dir)
            while True:
                try:
                    result = self.run_once()
                    LOG.info("publisher cycle: %s", json.dumps(result, sort_keys=True))
                except Exception as exc:
                    LOG.exception("publisher cycle failed (%s)", type(exc).__name__)
                    self._notify(f"publisher-cycle:{datetime.now(timezone.utc).strftime('%Y-%m-%d-%H-%M')}", {
                        "event": "publisher_cycle_failed", "error_type": type(exc).__name__,
                    })
                time.sleep(max(1, self.config.poll_seconds))

def approve_video(
    file_path: str | Path,
    watch_dir: str | Path,
    *,
    destinations: list[dict[str, str]],
    ai_generated: bool,
    realistic_alteration: bool,
    disclosure_reviewed: bool = False,
) -> Path:
    """Bind a human approval to exact source bytes and explicit destinations."""
    root = Path(watch_dir).expanduser().resolve()
    candidate = Path(file_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise ValueError("approval target must not be a symbolic link")
    candidate = candidate.resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("approval target must remain inside the watched folder") from exc
    if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("approval target must be a regular supported video file inside the watched folder")
    if not destinations:
        raise ValueError("at least one explicit publishing destination is required")
    for destination in destinations:
        if destination.get("platform") not in PLATFORMS or not str(destination.get("account_id") or "").strip():
            raise ValueError("every destination must contain a supported platform and account_id")
    if (ai_generated or realistic_alteration) and not disclosure_reviewed:
        raise ValueError("AI-generated or realistically altered media requires explicit disclosure acknowledgement")
    # Probe here to catch obvious corrupt media before writing an approval sidecar.
    probe_media(candidate)
    digest = sha256_file(candidate)
    sidecar = Path(str(candidate) + ".publish.json")
    if sidecar.is_symlink():
        raise ValueError("approval sidecar must not be a symbolic link")
    payload: dict[str, Any] = {}
    if sidecar.is_file():
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("existing sidecar must contain a JSON object")
        payload = loaded
    payload.setdefault("title", candidate.stem[:100])
    payload.setdefault("description", "")
    payload.setdefault("tags", [])
    payload.setdefault("privacy_status", "private")
    payload.setdefault("category_id", "22")
    payload.update({
        "publication_id": uuid.uuid4().hex,
        "approved_sha256": digest,
        "publish_approved": True,
        "rights_reviewed": True,
        "ai_generated": bool(ai_generated),
        "realistic_alteration": bool(realistic_alteration),
        "disclosure_reviewed": bool(disclosure_reviewed),
        "destinations": destinations,
    })
    temporary = sidecar.with_name(f".{sidecar.name}.{uuid.uuid4().hex}.partial")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, sidecar)
    finally:
        temporary.unlink(missing_ok=True)
    return sidecar

__all__ = [
    "Adapter", "AdapterError", "AdapterUpdate", "PLATFORMS", "PublisherConfig",
    "PublisherError", "PublisherService", "PublisherStore", "STATES",
    "approve_video", "configured_platforms", "decode_check", "parse_destinations",
    "probe_media", "quality_scan", "sha256_file",
]
