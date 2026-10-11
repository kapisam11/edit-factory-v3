"""Durable, single-host publishing of existing videos from a watched folder.

Each source file is immutable after a stability window and gets one persistent
job per destination account. The SQLite lease fences concurrent workers. Remote
state is checkpointed between API stages; ambiguous public-post outcomes are
never blindly retried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import mimetypes
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger("ai_video_factory.existing_file_publisher")
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
TERMINAL_STATES = {"PUBLISHED", "FAILED", "BLOCKED", "NEEDS_RECONCILIATION"}


class PublishError(RuntimeError):
    """A known error returned by a publishing API."""


class NeedsReconciliation(PublishError):
    """The remote side effect may have happened; never create another post blindly."""


class PermanentPublishError(PublishError):
    """A non-retryable validation, permission, or policy error."""


@dataclass(frozen=True)
class PublisherConfig:
    watch_dir: Path
    state_db: Path
    poll_seconds: int = 30
    stable_seconds: int = 60
    max_file_bytes: int = 1_073_741_824
    max_duration_seconds: float = 900.0
    max_videos_per_account_per_day: int = 1
    publish_time_utc: str = "12:00"
    max_attempts: int = 6
    retry_base_seconds: int = 60
    allow_public: bool = False
    platforms: tuple[str, ...] = ("youtube",)

    @classmethod
    def from_environment(cls) -> "PublisherConfig":
        root = Path(os.environ.get("AIVF_STATE_DIR", "./state")).expanduser().resolve()
        watch = Path(os.environ.get("AIVF_PUBLISH_WATCH_DIR", "./publish-watch")).expanduser().resolve()
        platform_values = tuple(
            x.strip().lower()
            for x in os.environ.get("AIVF_FILE_PUBLISH_PLATFORMS", "youtube").split(",")
            if x.strip()
        )
        invalid = set(platform_values) - {"youtube", "instagram", "tiktok"}
        if invalid:
            raise ValueError(f"Unsupported publishing platform(s): {', '.join(sorted(invalid))}")
        publish_time = os.environ.get("AIVF_FILE_PUBLISH_TIME_UTC", "12:00").strip()
        _parse_clock(publish_time)
        return cls(
            watch_dir=watch,
            state_db=Path(os.environ.get("AIVF_FILE_PUBLISH_DB", str(root / "file-publishing.sqlite"))).expanduser().resolve(),
            poll_seconds=max(10, int(os.environ.get("AIVF_FILE_PUBLISH_POLL_SECONDS", "30"))),
            stable_seconds=max(10, int(os.environ.get("AIVF_FILE_PUBLISH_STABLE_SECONDS", "60"))),
            max_file_bytes=max(1, int(os.environ.get("AIVF_FILE_PUBLISH_MAX_BYTES", str(1_073_741_824)))),
            max_duration_seconds=max(1.0, float(os.environ.get("AIVF_FILE_PUBLISH_MAX_DURATION_SECONDS", "900"))),
            max_videos_per_account_per_day=max(1, int(os.environ.get("AIVF_FILE_PUBLISH_MAX_PER_ACCOUNT_DAILY", "1"))),
            publish_time_utc=publish_time,
            max_attempts=max(1, int(os.environ.get("AIVF_FILE_PUBLISH_MAX_ATTEMPTS", "6"))),
            retry_base_seconds=max(10, int(os.environ.get("AIVF_FILE_PUBLISH_RETRY_BASE_SECONDS", "60"))),
            allow_public=os.environ.get("AIVF_FILE_PUBLISH_ALLOW_PUBLIC", "0").strip() == "1",
            platforms=platform_values or ("youtube",),
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: Optional[datetime] = None) -> str:
    return (value or _utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_clock(value: str) -> datetime_time:
    try:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError
        return datetime_time(hour=int(parts[0]), minute=int(parts[1]), tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError("AIVF_FILE_PUBLISH_TIME_UTC must be HH:MM in UTC") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_ratio(value: str) -> float:
    try:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError, AttributeError):
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0


def _probe_media(path: Path, *, max_bytes: int, max_duration: float) -> dict[str, Any]:
    size = path.stat().st_size
    if size <= 0:
        raise PermanentPublishError("Video file is empty")
    if size > max_bytes:
        raise PermanentPublishError(f"Video exceeds configured file size limit ({size} > {max_bytes} bytes)")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise PermanentPublishError("ffprobe is required for automatic publishing but was not found on PATH")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration,size:"
             "stream=codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate",
             "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise PermanentPublishError(f"ffprobe could not validate video: {exc}") from exc
    streams = payload.get("streams") or []
    video_stream = next((item for item in streams if item.get("codec_type") == "video"), None)
    if not isinstance(video_stream, dict):
        raise PermanentPublishError("Media probe did not find a video stream")
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
    except (TypeError, ValueError) as exc:
        raise PermanentPublishError("Media probe returned invalid duration or dimensions") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise PermanentPublishError("Video duration must be a finite positive number")
    if duration > max_duration:
        raise PermanentPublishError(f"Video duration {duration:.2f}s exceeds configured limit {max_duration:.2f}s")
    if width <= 0 or height <= 0 or width > 7680 or height > 4320:
        raise PermanentPublishError(f"Unsupported or unsafe video dimensions: {width}x{height}")
    fps = _parse_ratio(str(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0"))
    if not math.isfinite(fps) or fps <= 0 or fps > 120:
        raise PermanentPublishError(f"Invalid or unsupported video frame rate: {fps}")
    return {
        "size_bytes": size, "duration_seconds": duration, "width": width,
        "height": height, "fps": fps, "video_codec": str(video_stream.get("codec_name") or ""),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
    }


def _load_metadata(path: Path) -> dict[str, Any]:
    sidecar = path.with_suffix(".publish.json")
    if not sidecar.is_file():
        raise FileNotFoundError(f"Required publishing sidecar is missing: {sidecar.name}")
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PermanentPublishError(f"Invalid sidecar {sidecar.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise PermanentPublishError(f"{sidecar.name} must contain a JSON object")
    return value


def _safe_asset(value: Any, *, watch_dir: Path, label: str) -> Optional[str]:
    if value in (None, ""):
        return None
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = watch_dir / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(watch_dir.resolve())
    except (OSError, ValueError) as exc:
        raise PermanentPublishError(f"{label} must be an existing file inside the watched folder") from exc
    if not resolved.is_file():
        raise PermanentPublishError(f"{label} must refer to a regular file")
    return str(resolved)


def _validate_metadata(metadata: Mapping[str, Any], *, config: PublisherConfig, watch_dir: Path) -> dict[str, Any]:
    result = dict(metadata)
    title = str(result.get("title") or "").strip()
    if not title:
        raise PermanentPublishError("Sidecar requires a non-empty title")
    if len(title) > 100:
        raise PermanentPublishError("Title exceeds YouTube's 100-character title limit")
    if result.get("rights_cleared") is not True:
        raise PermanentPublishError("Publishing blocked: sidecar must set rights_cleared to true")
    requested = result.get("platforms", config.platforms)
    if not isinstance(requested, list) or not requested:
        raise PermanentPublishError("platforms must be a non-empty list")
    platforms = tuple(str(p).strip().lower() for p in requested)
    unknown = set(platforms) - {"youtube", "instagram", "tiktok"}
    if unknown:
        raise PermanentPublishError(f"Unknown platform(s): {', '.join(sorted(unknown))}")
    result["platforms"] = list(dict.fromkeys(platforms))
    result["title"] = title
    result["description"] = str(result.get("description") or result.get("caption") or "")
    result["tags"] = [str(x).strip() for x in result.get("tags", []) if str(x).strip()][:30] if isinstance(result.get("tags", []), list) else []
    result["ai_generated"] = bool(result.get("ai_generated", False))
    result["realistic_alteration"] = bool(result.get("realistic_alteration", False))
    result["disclosure_reviewed"] = bool(result.get("disclosure_reviewed", False))
    result["public_publish_approved"] = result.get("public_publish_approved") is True
    result["thumbnail_path"] = _safe_asset(result.get("thumbnail_path"), watch_dir=watch_dir, label="thumbnail_path")
    result["caption_path"] = _safe_asset(result.get("caption_path"), watch_dir=watch_dir, label="caption_path")
    result["caption_language"] = str(result.get("caption_language") or "en")[:12]
    yt_privacy = str(result.get("youtube_privacy_status") or "private").lower()
    if yt_privacy not in {"private", "unlisted", "public"}:
        raise PermanentPublishError("youtube_privacy_status must be private, unlisted, or public")
    result["youtube_privacy_status"] = yt_privacy
    tiktok_privacy = str(result.get("tiktok_privacy_level") or "SELF_ONLY")
    if tiktok_privacy not in {"PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"}:
        raise PermanentPublishError("Invalid tiktok_privacy_level")
    result["tiktok_privacy_level"] = tiktok_privacy
    result["instagram_share_to_feed"] = bool(result.get("instagram_share_to_feed", True))
    for platform in result["platforms"]:
        is_public = (
            platform == "instagram"
            or (platform == "youtube" and result["youtube_privacy_status"] == "public")
            or (platform == "tiktok" and result["tiktok_privacy_level"] == "PUBLIC_TO_EVERYONE")
        )
        if is_public and not (config.allow_public and result["public_publish_approved"]):
            raise PermanentPublishError(
                f"Public publishing to {platform} requires AIVF_FILE_PUBLISH_ALLOW_PUBLIC=1 "
                "and public_publish_approved=true in the sidecar"
            )
    return result


def _http_json(method: str, url: str, *, headers: Optional[dict[str, str]] = None,
               params: Optional[dict[str, Any]] = None, data: Any = None,
               json_body: Optional[dict[str, Any]] = None, timeout: int = 60) -> dict[str, Any]:
    try:
        response = requests.request(method, url, headers=headers, params=params, data=data, json=json_body, timeout=timeout)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise NeedsReconciliation(f"Network outcome is ambiguous for {method} {urlparse(url).netloc}") from exc
    except requests.RequestException as exc:
        raise PublishError(f"Request failed for {urlparse(url).netloc}: {exc}") from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        message = str(payload.get("error", {}).get("message") if isinstance(payload.get("error"), dict) else payload.get("message") or response.text or f"HTTP {response.status_code}")
        if response.status_code in {400, 401, 403, 404, 422}:
            raise PermanentPublishError(f"Publishing API rejected the request ({response.status_code}): {message[:500]}")
        raise PublishError(f"Publishing API returned HTTP {response.status_code}: {message[:500]}")
    if isinstance(payload.get("error"), dict):
        error = payload["error"]
        code = str(error.get("code") or "")
        if code and code.lower() not in {"ok", "success"}:
            raise PermanentPublishError(f"Publishing API error {code}: {str(error.get('message') or '')[:500]}")
    if not isinstance(payload, dict):
        raise PublishError("Publishing API returned an unexpected response")
    return payload


def _youtube_account_id() -> str:
    from .youtube_publisher import UPLOAD_SCOPES, _paths, _service
    client, token = _paths(None, None)
    service = _service("youtube", "v3", client, token, UPLOAD_SCOPES)
    payload = service.channels().list(part="id", mine=True).execute()
    items = payload.get("items") or []
    if not items or not items[0].get("id"):
        raise PermanentPublishError("The configured YouTube token did not resolve to a channel ID")
    return str(items[0]["id"])


def _account_ids() -> dict[str, str]:
    identities: dict[str, str] = {}
    try:
        identities["youtube"] = _youtube_account_id()
    except Exception as exc:
        logger.warning("YouTube destination is not ready: %s", exc)
        identities["youtube"] = "unconfigured-youtube"
    identities["instagram"] = os.environ.get("INSTAGRAM_USER_ID", "").strip() or "unconfigured-instagram"
    identities["tiktok"] = os.environ.get("TIKTOK_OPEN_ID", "").strip() or "unconfigured-tiktok"
    return identities


class PublishStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS publish_candidates (
                    source_path TEXT PRIMARY KEY,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    sidecar_size INTEGER NOT NULL,
                    sidecar_mtime_ns INTEGER NOT NULL,
                    stable_since REAL NOT NULL,
                    last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS file_publish_jobs (
                    job_id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    account_key TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    lease_token TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0,
                    remote_state_json TEXT NOT NULL DEFAULT '{}',
                    remote_id TEXT NOT NULL DEFAULT '',
                    remote_url TEXT NOT NULL DEFAULT '',
                    published_at TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    UNIQUE(source_sha256, platform, account_key)
                );
                CREATE INDEX IF NOT EXISTS idx_file_publish_due
                    ON file_publish_jobs(state, next_attempt_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_file_publish_account_day
                    ON file_publish_jobs(platform, account_key, state, published_at);
                CREATE TABLE IF NOT EXISTS file_publish_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("PRAGMA user_version=1")

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _event(self, conn: sqlite3.Connection, job_id: str, event: str, detail: str = "") -> None:
        conn.execute(
            "INSERT INTO file_publish_events(job_id,event,detail,created_at) VALUES(?,?,?,?)",
            (job_id, event, detail[:1500], _utc_text()),
        )

    def candidate_is_stable(self, source: Path, sidecar: Path, stable_seconds: int) -> bool:
        source_stat = source.stat()
        side_stat = sidecar.stat()
        now = time.time()
        key = str(source.resolve())
        values = (source_stat.st_size, source_stat.st_mtime_ns, side_stat.st_size, side_stat.st_mtime_ns)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT * FROM publish_candidates WHERE source_path=?", (key,)).fetchone()
            if previous is None or tuple(previous[k] for k in ("size_bytes", "mtime_ns", "sidecar_size", "sidecar_mtime_ns")) != values:
                conn.execute("""
                    INSERT INTO publish_candidates(source_path,size_bytes,mtime_ns,sidecar_size,sidecar_mtime_ns,stable_since,last_seen)
                    VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(source_path) DO UPDATE SET size_bytes=excluded.size_bytes,mtime_ns=excluded.mtime_ns,
                    sidecar_size=excluded.sidecar_size,sidecar_mtime_ns=excluded.sidecar_mtime_ns,
                    stable_since=excluded.stable_since,last_seen=excluded.last_seen
                """, (key, *values, now, now))
                return False
            conn.execute("UPDATE publish_candidates SET last_seen=? WHERE source_path=?", (now, key))
            return now - float(previous["stable_since"]) >= stable_seconds

    def enqueue(self, *, source: Path, digest: str, platform: str, account_key: str,
                metadata: Mapping[str, Any], blocked: str = "") -> str:
        identity = hashlib.sha256(f"{digest}|{platform}|{account_key}".encode()).hexdigest()
        job_id = f"file-{identity[:32]}"
        now = _utc_text()
        state = "BLOCKED" if blocked else "PENDING"
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM file_publish_jobs WHERE job_id=?", (job_id,)).fetchone()
            serialized = json.dumps(dict(metadata), sort_keys=True, ensure_ascii=False)
            if row is None:
                conn.execute("""
                    INSERT INTO file_publish_jobs(job_id,source_path,source_sha256,platform,account_key,
                        metadata_json,state,created_at,updated_at,last_error)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (job_id, str(source.resolve()), digest, platform, account_key, serialized, state, now, now, blocked))
                self._event(conn, job_id, "queued" if not blocked else "blocked", blocked or f"destination={platform}")
            elif row["state"] == "BLOCKED" and not blocked:
                conn.execute("""
                    UPDATE file_publish_jobs SET metadata_json=?,state='PENDING',updated_at=?,last_error='',
                        next_attempt_at=0 WHERE job_id=?
                """, (serialized, now, job_id))
                self._event(conn, job_id, "unblocked", "Sidecar/configuration corrected; no prior post was created")
            return job_id

    def claim_next(self, *, now_epoch: float, now_utc: datetime, config: PublisherConfig,
                   processed: set[str]) -> Optional[dict[str, Any]]:
        clock = _parse_clock(config.publish_time_utc)
        slot = datetime.combine(now_utc.date(), clock)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # A crashed worker's lease is reclaimed only if the job has no
            # ambiguous final-publication call in flight.
            expired = conn.execute("""
                SELECT job_id,platform,remote_state_json FROM file_publish_jobs
                WHERE state='UPLOADING' AND lease_until < ?
            """, (now_epoch,)).fetchall()
            for item in expired:
                remote = json.loads(item["remote_state_json"] or "{}")
                stage = str(remote.get("stage") or "")
                if stage in {"ig_publishing", "tiktok_init_in_flight", "tiktok_upload_in_flight"}:
                    state = "NEEDS_RECONCILIATION"
                    error = "Worker stopped during an ambiguous remote publishing operation; automatic retry disabled"
                else:
                    state = "RETRY"
                    error = "Recovered expired worker lease"
                conn.execute("""
                    UPDATE file_publish_jobs SET state=?,lease_token='',lease_until=0,
                    next_attempt_at=?,updated_at=?,last_error=? WHERE job_id=?
                """, (state, now_epoch if state == "RETRY" else 0, _utc_text(), error, item["job_id"]))
                self._event(conn, item["job_id"], "lease_recovered", error)
            # Remote-processing polls may continue even after this account has
            # already published its one allowed post for the day.
            rows = conn.execute("""
                SELECT * FROM file_publish_jobs
                WHERE state IN ('WAITING_REMOTE','RETRY','PENDING')
                  AND next_attempt_at <= ?
                ORDER BY CASE state WHEN 'WAITING_REMOTE' THEN 0 ELSE 1 END, created_at, job_id
                LIMIT 100
            """, (now_epoch,)).fetchall()
            for row in rows:
                if row["job_id"] in processed:
                    continue
                if row["state"] != "WAITING_REMOTE":
                    if now_utc < slot:
                        continue
                    published_today = conn.execute("""
                        SELECT COUNT(*) FROM file_publish_jobs
                        WHERE platform=? AND account_key=? AND state='PUBLISHED'
                          AND published_at >= ?
                    """, (row["platform"], row["account_key"], now_utc.strftime("%Y-%m-%dT00:00:00Z"))).fetchone()[0]
                    if int(published_today) >= config.max_videos_per_account_per_day:
                        continue
                token = secrets.token_hex(16)
                changed = conn.execute("""
                    UPDATE file_publish_jobs SET state='UPLOADING',lease_token=?,lease_until=?,
                        attempts=attempts+1,updated_at=?
                    WHERE job_id=? AND state=?
                """, (token, now_epoch + max(120, config.poll_seconds * 4), _utc_text(), row["job_id"], row["state"])).rowcount
                if changed:
                    claimed = dict(row)
                    claimed["lease_token"] = token
                    claimed["attempts"] = int(row["attempts"]) + 1
                    self._event(conn, row["job_id"], "attempt_started", f"platform={row['platform']} attempt={claimed['attempts']}")
                    return claimed
            return None

    def checkpoint(self, job_id: str, token: str, remote_state: Mapping[str, Any]) -> None:
        with self.connect() as conn:
            changed = conn.execute("""
                UPDATE file_publish_jobs SET remote_state_json=?,updated_at=?,lease_until=?
                WHERE job_id=? AND state='UPLOADING' AND lease_token=?
            """, (json.dumps(dict(remote_state), sort_keys=True), _utc_text(),
                  time.time() + 300, job_id, token)).rowcount
            if not changed:
                raise RuntimeError("Publishing lease lost; refusing to commit a stale worker checkpoint")

    def finish(self, job: Mapping[str, Any], *, state: str, remote_state: Mapping[str, Any],
               remote_id: str = "", remote_url: str = "", error: str = "",
               retry_at: float = 0.0) -> None:
        job_id, token = str(job["job_id"]), str(job["lease_token"])
        published_at = _utc_text() if state == "PUBLISHED" else None
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute("""
                UPDATE file_publish_jobs SET state=?,remote_state_json=?,remote_id=?,remote_url=?,
                    published_at=COALESCE(?,published_at),last_error=?,next_attempt_at=?,lease_token='',
                    lease_until=0,updated_at=?
                WHERE job_id=? AND state='UPLOADING' AND lease_token=?
            """, (state, json.dumps(dict(remote_state), sort_keys=True), remote_id, remote_url,
                  published_at, error[:1500], retry_at, _utc_text(), job_id, token)).rowcount
            if not changed:
                raise RuntimeError("Publishing lease lost; stale worker cannot finalize job")
            self._event(conn, job_id, state.lower(), error or remote_id or remote_url)

    def status(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT job_id,source_path,platform,account_key,state,attempts,remote_id,remote_url,
                       published_at,next_attempt_at,last_error,created_at,updated_at
                FROM file_publish_jobs ORDER BY created_at DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]


def _platform_adapter(platform: str, account_key: str) -> Callable[..., dict[str, Any]]:
    if platform == "youtube":
        from .youtube_publisher import upload_video

        def publish_youtube(path: Path, metadata: Mapping[str, Any], remote: dict[str, Any],
                            checkpoint: Callable[[dict[str, Any]], None], job_id: str) -> dict[str, Any]:
            if account_key.startswith("unconfigured-"):
                raise PermanentPublishError("YouTube credentials are not configured; authorize the intended channel first")
            if remote.get("stage") == "youtube_upload_in_flight":
                # upload_video's persistent idempotency journal reconciles its
                # own ambiguous upload. Re-use the same key on every retry.
                pass
            next_remote = dict(remote)
            next_remote["stage"] = "youtube_upload_in_flight"
            checkpoint(next_remote)
            result = upload_video(
                str(path), title=str(metadata["title"]),
                description=str(metadata.get("description") or ""),
                tags=list(metadata.get("tags") or []),
                privacy_status=str(metadata.get("youtube_privacy_status") or "private"),
                thumbnail_path=metadata.get("thumbnail_path"),
                caption_path=metadata.get("caption_path"),
                caption_language=str(metadata.get("caption_language") or "en"),
                ai_generated=bool(metadata.get("ai_generated")),
                realistic_alteration=bool(metadata.get("realistic_alteration")),
                disclosure_reviewed=bool(metadata.get("disclosure_reviewed")),
                idempotency_key=f"folder-{job_id}",
            )
            video_id = str(result.get("video_id") or "")
            if not video_id:
                raise PublishError("YouTube uploader returned no verified video ID")
            return {"state": "PUBLISHED", "remote_id": video_id,
                    "remote_url": str(result.get("url") or f"https://www.youtube.com/watch?v={video_id}"),
                    "remote_state": {"stage": "published", "video_id": video_id}}
        return publish_youtube
    if platform == "instagram":
        return _publish_instagram
    if platform == "tiktok":
        return _publish_tiktok
    raise PermanentPublishError(f"No adapter exists for platform {platform}")


def _instagram_account_identity() -> str:
    return os.environ.get("INSTAGRAM_USER_ID", "").strip()


def _instagram_api_base() -> str:
    version = os.environ.get("INSTAGRAM_GRAPH_API_VERSION", "v23.0").strip()
    if not version.startswith("v") or not version[1:].replace(".", "").isdigit():
        raise PermanentPublishError("INSTAGRAM_GRAPH_API_VERSION must look like v23.0")
    return f"https://graph.facebook.com/{version}"


def _instagram_status(container_id: str, token: str) -> dict[str, Any]:
    return _http_json("GET", f"{_instagram_api_base()}/{container_id}",
                      params={"fields": "status_code,status", "access_token": token})


def _publish_instagram(path: Path, metadata: Mapping[str, Any], remote: dict[str, Any],
                       checkpoint: Callable[[dict[str, Any]], None], job_id: str) -> dict[str, Any]:
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    user_id = _instagram_account_identity()
    if not token or not user_id or user_id.startswith("unconfigured-"):
        raise PermanentPublishError("Set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_USER_ID for a professional Instagram account")
    if path.stat().st_size > 1_000_000_000:
        raise PermanentPublishError("Instagram Reels publishing has a 1 GB video file limit")
    stage = str(remote.get("stage") or "")
    version = os.environ.get("INSTAGRAM_GRAPH_API_VERSION", "v23.0").strip()
    base = _instagram_api_base()
    if stage == "ig_publishing":
        raise NeedsReconciliation("Instagram publish response was interrupted; verify the account before any retry")
    if not stage or stage == "ig_init_in_flight":
        # A missing container ID can leave an orphan draft container, but not a
        # public Reel. Recreating this stage cannot duplicate a public post.
        remote = {"stage": "ig_init_in_flight"}
        checkpoint(remote)
        caption = str(metadata.get("instagram_caption") or metadata.get("description") or metadata.get("title") or "")[:2200]
        created = _http_json(
            "POST", f"{base}/{user_id}/media",
            data={"media_type": "REELS", "upload_type": "resumable",
                  "caption": caption, "share_to_feed": "true" if metadata.get("instagram_share_to_feed", True) else "false"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        container_id = str(created.get("id") or "")
        if not container_id:
            raise PublishError("Instagram API did not return a media container ID")
        upload_uri = str(created.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{version}/{container_id}")
        if urlparse(upload_uri).hostname not in {"rupload.facebook.com", "graph.facebook.com"}:
            raise PermanentPublishError("Instagram returned an upload URL with an unexpected host")
        remote = {"stage": "ig_upload_pending", "container_id": container_id, "upload_uri": upload_uri}
        checkpoint(remote)
        stage = "ig_upload_pending"
    container_id = str(remote.get("container_id") or "")
    if not container_id:
        raise NeedsReconciliation("Instagram container state is missing; automatic retry is disabled")
    if stage in {"ig_upload_in_flight", "ig_processing"}:
        status = _instagram_status(container_id, token)
        status_code = str(status.get("status_code") or "").upper()
        if status_code == "FINISHED":
            remote = {**remote, "stage": "ig_publish_pending"}
            checkpoint(remote)
            stage = "ig_publish_pending"
        elif status_code in {"ERROR", "EXPIRED"}:
            raise PermanentPublishError(f"Instagram container cannot be published: {status.get('status') or status_code}")
        else:
            remote = {**remote, "stage": "ig_processing"}
            checkpoint(remote)
            return {"state": "WAITING_REMOTE", "remote_id": container_id, "remote_url": "",
                    "remote_state": remote}
    if stage == "ig_upload_pending":
        remote = {**remote, "stage": "ig_upload_in_flight"}
        checkpoint(remote)
        size = path.stat().st_size
        upload_uri = str(remote["upload_uri"])
        try:
            with path.open("rb") as video:
                response = requests.post(
                    upload_uri,
                    headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
                             "Content-Type": "application/octet-stream"},
                    data=video, timeout=300,
                )
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not response.ok:
                raise PublishError(f"Instagram resumable upload returned HTTP {response.status_code}: {str(payload)[:400]}")
        except requests.RequestException as exc:
            # On resume, query the container before re-sending bytes. A partial
            # upload must never be guessed at or blindly restarted.
            raise NeedsReconciliation(f"Instagram file upload was interrupted; container status must be reconciled: {exc}") from exc
        remote = {**remote, "stage": "ig_processing"}
        checkpoint(remote)
        stage = "ig_processing"
        status = _instagram_status(container_id, token)
        status_code = str(status.get("status_code") or "").upper()
        if status_code != "FINISHED":
            if status_code in {"ERROR", "EXPIRED"}:
                raise PermanentPublishError(f"Instagram media processing failed: {status.get('status') or status_code}")
            return {"state": "WAITING_REMOTE", "remote_id": container_id, "remote_url": "", "remote_state": remote}
        remote = {**remote, "stage": "ig_publish_pending"}
        checkpoint(remote)
        stage = "ig_publish_pending"
    if stage == "ig_publish_pending":
        # Persist the point of no automatic return before the remote publish.
        # A crash or network timeout from this stage requires account-level review.
        remote = {**remote, "stage": "ig_publishing"}
        checkpoint(remote)
        result = _http_json("POST", f"{base}/{user_id}/media_publish",
                            data={"creation_id": container_id, "access_token": token}, timeout=90)
        media_id = str(result.get("id") or "")
        if not media_id:
            raise NeedsReconciliation("Instagram publish response had no media ID")
        remote = {**remote, "stage": "published", "media_id": media_id}
        checkpoint(remote)
        return {"state": "PUBLISHED", "remote_id": media_id,
                "remote_url": "", "remote_state": remote}
    return {"state": "WAITING_REMOTE", "remote_id": container_id, "remote_url": "", "remote_state": remote}


def _tiktok_api(path: str, token: str, *, body: dict[str, Any]) -> dict[str, Any]:
    result = _http_json("POST", f"https://open.tiktokapis.com/v2/{path.lstrip('/')}",
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
                        json_body=body, timeout=90)
    error = result.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "")
        if code and code.lower() not in {"ok", "success"}:
            raise PermanentPublishError(f"TikTok API error {code}: {str(error.get('message') or '')[:500]}")
    return result


def _tiktok_status(publish_id: str, token: str) -> dict[str, Any]:
    result = _tiktok_api("post/publish/status/fetch/", token, body={"publish_id": publish_id})
    return result.get("data") if isinstance(result.get("data"), dict) else {}


def _publish_tiktok(path: Path, metadata: Mapping[str, Any], remote: dict[str, Any],
                    checkpoint: Callable[[dict[str, Any]], None], job_id: str) -> dict[str, Any]:
    token = os.environ.get("TIKTOK_ACCESS_TOKEN", "").strip()
    open_id = os.environ.get("TIKTOK_OPEN_ID", "").strip()
    if not token or not open_id or open_id.startswith("unconfigured-"):
        raise PermanentPublishError("Set TIKTOK_ACCESS_TOKEN and TIKTOK_OPEN_ID for the authorized TikTok account")
    if path.suffix.lower() not in {".mp4", ".mov", ".webm"}:
        raise PermanentPublishError("TikTok direct upload accepts MP4, MOV, or WebM in this workflow")
    size = path.stat().st_size
    if size > 4_000_000_000:
        raise PermanentPublishError("TikTok upload exceeds the configured 4 GB safety limit")
    stage = str(remote.get("stage") or "")
    if stage == "tiktok_init_in_flight":
        raise NeedsReconciliation("TikTok initialization response was interrupted and publish_id was not saved; do not retry blindly")
    if stage == "tiktok_upload_in_flight":
        raise NeedsReconciliation("TikTok file transfer was interrupted between remote acknowledgement and checkpoint; reconcile the existing publish_id before retry")
    if not stage:
        remote = {"stage": "tiktok_init_in_flight"}
        checkpoint(remote)
        creator = _tiktok_api("post/publish/creator_info/query/", token, body={}).get("data") or {}
        allowed = creator.get("privacy_level_options") or []
        privacy = str(metadata.get("tiktok_privacy_level") or "SELF_ONLY")
        if privacy not in allowed:
            raise PermanentPublishError(f"TikTok privacy setting {privacy} is not permitted for this creator")
        chunk_size = 64 * 1024 * 1024
        total_chunks = int(math.ceil(size / chunk_size))
        init = _tiktok_api("post/publish/video/init/", token, body={
            "post_info": {
                "title": str(metadata.get("tiktok_caption") or metadata.get("description") or metadata.get("title") or "")[:2200],
                "privacy_level": privacy,
                "disable_comment": bool(metadata.get("disable_comment", False)),
                "disable_duet": bool(metadata.get("disable_duet", False)),
                "disable_stitch": bool(metadata.get("disable_stitch", False)),
            },
            "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                            "chunk_size": min(chunk_size, size), "total_chunk_count": total_chunks},
        })
        data = init.get("data") or {}
        publish_id = str(data.get("publish_id") or "")
        upload_url = str(data.get("upload_url") or "")
        if not publish_id or not upload_url:
            raise NeedsReconciliation("TikTok did not return a durable publish_id/upload_url; inspect the creator account before retry")
        if urlparse(upload_url).hostname not in {"open-upload.tiktokapis.com", "upload.tiktokapis.com"}:
            raise PermanentPublishError("TikTok returned an upload URL with an unexpected host")
        remote = {"stage": "tiktok_upload_pending", "publish_id": publish_id, "upload_url": upload_url,
                  "chunk_size": min(chunk_size, size), "next_chunk": 0, "size_bytes": size}
        checkpoint(remote)
        stage = "tiktok_upload_pending"
    publish_id = str(remote.get("publish_id") or "")
    if not publish_id:
        raise NeedsReconciliation("TikTok publish_id is missing; automatic retry is disabled")
    if stage == "tiktok_upload_pending":
        chunk_size = int(remote.get("chunk_size") or (64 * 1024 * 1024))
        next_chunk = int(remote.get("next_chunk") or 0)
        total_size = path.stat().st_size
        total_chunks = int(math.ceil(total_size / chunk_size))
        with path.open("rb") as handle:
            handle.seek(next_chunk * chunk_size)
            while next_chunk < total_chunks:
                start = next_chunk * chunk_size
                block = handle.read(min(chunk_size, total_size - start))
                if not block:
                    raise PublishError("Unexpected end of file while uploading to TikTok")
                end = start + len(block) - 1
                remote = {**remote, "stage": "tiktok_upload_in_flight", "in_flight_chunk": next_chunk}
                checkpoint(remote)
                try:
                    response = requests.put(
                        str(remote["upload_url"]), data=block,
                        headers={"Content-Type": mimetypes.guess_type(path.name)[0] or "video/mp4",
                                 "Content-Length": str(len(block)),
                                 "Content-Range": f"bytes {start}-{end}/{total_size}"},
                        timeout=300,
                    )
                except requests.RequestException as exc:
                    raise NeedsReconciliation(f"TikTok upload outcome is uncertain for chunk {next_chunk}: {exc}") from exc
                if response.status_code not in {200, 201, 206, 308}:
                    raise PublishError(f"TikTok upload server returned HTTP {response.status_code}: {response.text[:300]}")
                next_chunk += 1
                remote = {**remote, "stage": "tiktok_upload_pending", "next_chunk": next_chunk}
                remote.pop("in_flight_chunk", None)
                checkpoint(remote)
        remote = {**remote, "stage": "tiktok_processing"}
        checkpoint(remote)
        stage = "tiktok_processing"
    if stage in {"tiktok_processing", "tiktok_published"}:
        status = _tiktok_status(publish_id, token)
        status_value = str(status.get("status") or "").upper()
        if status_value == "PUBLISH_COMPLETE":
            raw_ids = status.get("publicly_available_post_id") or status.get("publicaly_available_post_id") or []
            post_id = str(raw_ids[0]) if isinstance(raw_ids, list) and raw_ids else publish_id
            username = str(status.get("creator_username") or "")
            url = f"https://www.tiktok.com/@{username}/video/{post_id}" if username and post_id != publish_id else ""
            remote = {**remote, "stage": "published", "post_id": post_id, "status": status_value}
            checkpoint(remote)
            return {"state": "PUBLISHED", "remote_id": post_id, "remote_url": url, "remote_state": remote}
        if status_value == "FAILED":
            raise PermanentPublishError(f"TikTok rejected the post: {str(status.get('fail_reason') or status.get('status_msg') or 'unknown failure')[:500]}")
        remote = {**remote, "stage": "tiktok_processing", "status": status_value}
        checkpoint(remote)
        return {"state": "WAITING_REMOTE", "remote_id": publish_id, "remote_url": "", "remote_state": remote}
    return {"state": "WAITING_REMOTE", "remote_id": publish_id, "remote_url": "", "remote_state": remote}


class ExistingFilePublisher:
    def __init__(self, config: Optional[PublisherConfig] = None) -> None:
        self.config = config or PublisherConfig.from_environment()
        self.config.watch_dir.mkdir(parents=True, exist_ok=True)
        self.store = PublishStore(self.config.state_db)
        self.account_ids = _account_ids()

    def scan_once(self) -> int:
        queued = 0
        for source in sorted(self.config.watch_dir.rglob("*")):
            if not source.is_file() or source.suffix.lower() not in VIDEO_SUFFIXES:
                continue
            if any(part.startswith(".") or part in {"processed", "archive"} for part in source.relative_to(self.config.watch_dir).parts):
                continue
            sidecar = source.with_suffix(".publish.json")
            if not sidecar.is_file():
                logger.info("Skipping %s: waiting for sidecar %s", source.name, sidecar.name)
                continue
            try:
                if not self.store.candidate_is_stable(source, sidecar, self.config.stable_seconds):
                    continue
                raw_metadata = _load_metadata(source)
                try:
                    metadata = _validate_metadata(raw_metadata, config=self.config, watch_dir=self.config.watch_dir)
                    block_reason = ""
                except PermanentPublishError as exc:
                    metadata = dict(raw_metadata)
                    metadata["title"] = str(metadata.get("title") or source.stem)
                    block_reason = str(exc)
                digest = _sha256_file(source)
                for platform in metadata.get("platforms", self.config.platforms):
                    platform = str(platform).lower()
                    account = self.account_ids.get(platform, f"unconfigured-{platform}")
                    if platform == "youtube" and account == "unconfigured-youtube":
                        platform_block = "YouTube credentials are not configured or could not identify the channel"
                    elif platform == "instagram" and account == "unconfigured-instagram":
                        platform_block = "Set INSTAGRAM_USER_ID and INSTAGRAM_ACCESS_TOKEN"
                    elif platform == "tiktok" and account == "unconfigured-tiktok":
                        platform_block = "Set TIKTOK_OPEN_ID and TIKTOK_ACCESS_TOKEN"
                    else:
                        platform_block = ""
                    reason = block_reason or platform_block
                    if not reason:
                        try:
                            _probe_media(source, max_bytes=self.config.max_file_bytes,
                                         max_duration=self.config.max_duration_seconds)
                        except PermanentPublishError as exc:
                            reason = str(exc)
                    self.store.enqueue(source=source, digest=digest, platform=platform,
                                       account_key=account, metadata=metadata, blocked=reason)
                    queued += 1
            except (OSError, PublishError, ValueError) as exc:
                logger.error("Could not queue %s: %s", source.name, exc)
        return queued

    def run_once(self, *, max_jobs: int = 50) -> int:
        self.scan_once()
        completed = 0
        processed: set[str] = set()
        for _ in range(max(1, max_jobs)):
            now = _utc_now()
            job = self.store.claim_next(now_epoch=time.time(), now_utc=now, config=self.config, processed=processed)
            if job is None:
                break
            processed.add(str(job["job_id"]))
            job_id = str(job["job_id"])
            try:
                source = Path(str(job["source_path"]))
                if not source.is_file():
                    raise PermanentPublishError("Source video disappeared before publishing")
                metadata = json.loads(job["metadata_json"])
                metadata = _validate_metadata(metadata, config=self.config, watch_dir=self.config.watch_dir)
                _probe_media(source, max_bytes=self.config.max_file_bytes, max_duration=self.config.max_duration_seconds)
                remote = json.loads(job.get("remote_state_json") or "{}")
                current_remote = remote
                def checkpoint(value: dict[str, Any]) -> None:
                    nonlocal current_remote
                    self.store.checkpoint(job_id, str(job["lease_token"]), value)
                    current_remote = dict(value)
                adapter = _platform_adapter(str(job["platform"]), str(job["account_key"]))
                outcome = adapter(source, metadata, remote, checkpoint, job_id)
                state = str(outcome.get("state") or "WAITING_REMOTE")
                if state not in {"PUBLISHED", "WAITING_REMOTE", "RETRY", "FAILED", "BLOCKED", "NEEDS_RECONCILIATION"}:
                    raise PublishError(f"Adapter returned unsupported state {state}")
                retry_at = time.time() + self.config.poll_seconds if state == "WAITING_REMOTE" else 0.0
                self.store.finish(job, state=state, remote_state=outcome.get("remote_state") or remote,
                                  remote_id=str(outcome.get("remote_id") or ""),
                                  remote_url=str(outcome.get("remote_url") or ""), retry_at=retry_at)
                completed += 1
                logger.info("%s %s %s", job_id, job["platform"], state)
            except NeedsReconciliation as exc:
                self.store.finish(job, state="NEEDS_RECONCILIATION",
                                  remote_state=current_remote if "current_remote" in locals() else json.loads(job.get("remote_state_json") or "{}"), error=str(exc))
                self._notify_failure(job, str(exc))
            except PermanentPublishError as exc:
                self.store.finish(job, state="FAILED",
                                  remote_state=current_remote if "current_remote" in locals() else json.loads(job.get("remote_state_json") or "{}"), error=str(exc))
                self._notify_failure(job, str(exc))
            except Exception as exc:
                attempts = int(job["attempts"])
                if attempts >= self.config.max_attempts:
                    state, retry_at = "FAILED", 0.0
                else:
                    state = "RETRY"
                    retry_at = time.time() + min(3600, self.config.retry_base_seconds * (2 ** max(0, attempts - 1)))
                remote = current_remote if "current_remote" in locals() else json.loads(job.get("remote_state_json") or "{}")
                self.store.finish(job, state=state, remote_state=remote, error=f"{type(exc).__name__}: {exc}", retry_at=retry_at)
                logger.exception("Publishing failed for %s", job_id)
                if state == "FAILED":
                    self._notify_failure(job, str(exc))
        return completed

    def daemon(self) -> None:
        logger.info("Watching %s; SQLite state at %s", self.config.watch_dir, self.config.state_db)
        while True:
            try:
                self.run_once()
            except Exception:
                logger.exception("File publisher iteration failed")
            time.sleep(self.config.poll_seconds)

    def _notify_failure(self, job: Mapping[str, Any], message: str) -> None:
        webhook = os.environ.get("AIVF_FILE_PUBLISH_FAILURE_WEBHOOK_URL", "").strip()
        if not webhook:
            logger.error("Publishing requires attention: job=%s platform=%s error=%s",
                         job.get("job_id"), job.get("platform"), message)
            return
        try:
            response = requests.post(webhook, json={
                "text": f"Edit Factory publishing requires attention\nJob: {job.get('job_id')}\nPlatform: {job.get('platform')}\nError: {message[:700]}"
            }, timeout=10)
            response.raise_for_status()
        except requests.RequestException:
            logger.exception("Failure webhook could not be delivered")



def _load_environment_file() -> None:
    """Load publisher-only secrets without adding a dotenv dependency.

    Existing process environment values win. The file is expected to be local,
    excluded from Git, and ACL-restricted on Windows.
    """
    configured = os.environ.get("AIVF_FILE_PUBLISH_ENV_FILE", "publish-folder.env")
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if "=" not in text:
            raise ValueError(f"Invalid publisher environment line {number}: expected KEY=VALUE")
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ValueError(f"Invalid publisher environment key on line {number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


def main(argv: Optional[list[str]] = None) -> int:
    try:
        _load_environment_file()
    except (OSError, ValueError) as exc:
        print(f"publisher environment error: {exc}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="Watch a folder and publish existing video files with durable per-platform state.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("daemon", help="watch continuously; start this at Windows logon")
    sub.add_parser("run-once", help="scan the folder and process currently due jobs")
    status = sub.add_parser("status", help="show recent publishing jobs")
    status.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("AIVF_FILE_PUBLISH_LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        manager = ExistingFilePublisher()
        if args.command == "daemon":
            manager.daemon()
            return 0
        if args.command == "run-once":
            manager.run_once()
            return 0
        print(json.dumps(manager.store.status(limit=max(1, min(500, args.limit))), indent=2, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
