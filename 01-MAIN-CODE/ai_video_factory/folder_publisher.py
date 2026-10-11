"""Durable, single-host intake and scheduled publishing for existing MP4 files.

This module only uploads to platforms with a real adapter. A named output profile is
never treated as evidence of an upload integration. SQLite state survives restarts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

PLATFORM_ALIASES = {
    "youtube": "youtube",
    "youtube_shorts": "youtube",
    "instagram": "instagram",
    "instagram_reels": "instagram",
    "tiktok": "tiktok",
}
IMPLEMENTED_UPLOADERS = {"youtube", "instagram", "tiktok"}
VIDEO_SUFFIXES = {".mp4"}
MAX_METADATA_BYTES = 256 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256_file(path: Path, max_bytes: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"file exceeds AIVF_FOLDER_MAX_INPUT_BYTES ({max_bytes} bytes)")
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FolderPublisherConfig:
    inbox_dir: Path
    state_dir: Path
    queue_db: Path
    stability_seconds: float = 60.0
    max_input_bytes: int = 2 * 1024 * 1024 * 1024
    max_duration_seconds: float = 900.0
    max_width: int = 3840
    max_height: int = 2160
    max_fps: float = 120.0
    max_videos_per_day: int = 1
    account_id: str = "default"
    instagram_account_id: str = "default"
    tiktok_account_id: str = "default"
    client_secrets_path: Optional[str] = None
    token_path: Optional[str] = None
    allow_public: bool = False

    @classmethod
    def from_environment(cls) -> "FolderPublisherConfig":
        state_dir = Path(os.environ.get("AIVF_STATE_DIR", "./state")).expanduser().resolve()
        inbox_dir = Path(os.environ.get("AIVF_INBOX_DIR", "./inbox")).expanduser().resolve()
        queue_db = Path(
            os.environ.get("AIVF_PUBLISH_QUEUE_DB", str(state_dir / "folder-publishing.sqlite3"))
        ).expanduser().resolve()

        def positive_int(name: str, default: int) -> int:
            try:
                value = int(os.environ.get(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if value < 1:
                raise ValueError(f"{name} must be >= 1")
            return value

        def positive_float(name: str, default: float) -> float:
            try:
                value = float(os.environ.get(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} must be a number") from exc
            if value <= 0:
                raise ValueError(f"{name} must be > 0")
            return value

        return cls(
            inbox_dir=inbox_dir,
            state_dir=state_dir,
            queue_db=queue_db,
            stability_seconds=max(0.0, float(os.environ.get("AIVF_FOLDER_STABILITY_SECONDS", "60"))),
            max_input_bytes=positive_int("AIVF_FOLDER_MAX_INPUT_BYTES", 2 * 1024 * 1024 * 1024),
            max_duration_seconds=positive_float("AIVF_FOLDER_MAX_DURATION_SECONDS", 900.0),
            max_width=positive_int("AIVF_FOLDER_MAX_WIDTH", 3840),
            max_height=positive_int("AIVF_FOLDER_MAX_HEIGHT", 2160),
            max_fps=positive_float("AIVF_FOLDER_MAX_FPS", 120.0),
            max_videos_per_day=positive_int("AIVF_FOLDER_MAX_VIDEOS_PER_DAY", 1),
            account_id=os.environ.get("AIVF_YOUTUBE_ACCOUNT_ID", "default").strip() or "default",
            instagram_account_id=(
                os.environ.get("AIVF_INSTAGRAM_ACCOUNT_ID")
                or os.environ.get("AIVF_INSTAGRAM_USER_ID")
                or "default"
            ).strip(),
            tiktok_account_id=(
                os.environ.get("AIVF_TIKTOK_ACCOUNT_ID")
                or os.environ.get("AIVF_TIKTOK_OPEN_ID")
                or "default"
            ).strip(),
            client_secrets_path=os.environ.get("YOUTUBE_CLIENT_SECRETS"),
            token_path=os.environ.get("YOUTUBE_TOKEN_PATH"),
            allow_public=os.environ.get("AIVF_FOLDER_ALLOW_PUBLIC", "0").strip() == "1",
        )


class FolderPublisher:
    """SQLite-backed single-file-per-run publisher with fail-closed review gates."""

    def __init__(self, config: FolderPublisherConfig):
        self.config = config
        self.config.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.config.queue_db.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def account_for(self, platform: str) -> str:
        if platform == "youtube":
            return self.config.account_id
        if platform == "instagram":
            return self.config.instagram_account_id
        if platform == "tiktok":
            return self.config.tiktok_account_id
        raise ValueError(f"unsupported publishing platform: {platform}")

    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.config.queue_db), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
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
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS observed_files (
                    path TEXT PRIMARY KEY,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    stable_since REAL NOT NULL,
                    last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publishing_jobs (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    metadata_sha256 TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_epoch REAL,
                    lease_until_epoch REAL,
                    remote_id TEXT,
                    remote_url TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT,
                    UNIQUE(platform, account_id, source_sha256)
                );
                CREATE INDEX IF NOT EXISTS idx_publishing_jobs_ready
                    ON publishing_jobs(state, next_attempt_epoch, created_at);
                CREATE INDEX IF NOT EXISTS idx_publishing_jobs_published
                    ON publishing_jobs(published_at, state);
                """
            )
            job_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(publishing_jobs)").fetchall()
            }
            if "lease_until_epoch" not in job_columns:
                conn.execute("ALTER TABLE publishing_jobs ADD COLUMN lease_until_epoch REAL")

    def _stable(self, path: Path, now: float) -> bool:
        try:
            stat_result = path.stat()
        except OSError:
            return False
        key = str(path.resolve())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT size_bytes, mtime_ns, stable_since FROM observed_files WHERE path=?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO observed_files(path,size_bytes,mtime_ns,stable_since,last_seen) "
                    "VALUES(?,?,?,?,?)",
                    (key, stat_result.st_size, stat_result.st_mtime_ns, now, now),
                )
                return self.config.stability_seconds == 0
            if row["size_bytes"] != stat_result.st_size or row["mtime_ns"] != stat_result.st_mtime_ns:
                conn.execute(
                    "UPDATE observed_files SET size_bytes=?,mtime_ns=?,stable_since=?,last_seen=? "
                    "WHERE path=?",
                    (stat_result.st_size, stat_result.st_mtime_ns, now, now, key),
                )
                return self.config.stability_seconds == 0
            conn.execute("UPDATE observed_files SET last_seen=? WHERE path=?", (now, key))
            return now - float(row["stable_since"]) >= self.config.stability_seconds

    def _probe_video(self, path: Path) -> dict[str, Any]:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height,avg_frame_rate",
                "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
        if not video_streams:
            raise ValueError("ffprobe found no video stream")
        video = video_streams[0]
        try:
            duration = float((payload.get("format") or {}).get("duration") or 0)
            width = int(video.get("width") or 0)
            height = int(video.get("height") or 0)
            numerator, denominator = str(video.get("avg_frame_rate") or "0/1").split("/", 1)
            fps = float(numerator) / max(float(denominator), 1.0)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            raise ValueError("ffprobe returned invalid duration, dimensions, or frame rate") from exc
        if duration <= 0 or duration > self.config.max_duration_seconds:
            raise ValueError(f"video duration must be between 0 and {self.config.max_duration_seconds:g} seconds")
        if width < 1 or height < 1 or width > self.config.max_width or height > self.config.max_height:
            raise ValueError(f"video dimensions must not exceed {self.config.max_width}x{self.config.max_height}")
        if fps <= 0 or fps > self.config.max_fps:
            raise ValueError(f"video frame rate must be between 0 and {self.config.max_fps:g} fps")
        return {"duration_seconds": duration, "width": width, "height": height, "fps": fps}

    def _read_metadata(self, path: Path) -> dict[str, Any]:
        sidecar = path.with_suffix(".json")
        if not sidecar.is_file() or sidecar.is_symlink() or not self._stable(sidecar, time.time()):
            raise FileNotFoundError(
                f"waiting for stable metadata sidecar: {sidecar.name} (create it before publishing)"
            )
        if sidecar.stat().st_size > MAX_METADATA_BYTES:
            raise ValueError(f"metadata sidecar exceeds {MAX_METADATA_BYTES} bytes")
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("metadata sidecar must contain a JSON object")
        title = str(payload.get("title") or "").strip()
        description = str(payload.get("description") or "").strip()
        tags = payload.get("tags", [])
        platforms = payload.get("platforms", [])
        if not title or len(title) > 100:
            raise ValueError("metadata title is required and must be at most 100 characters")
        if len(description) > 5000:
            raise ValueError("metadata description must be at most 5000 characters")
        if not isinstance(tags, list) or len(tags) > 30 or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("metadata tags must be a list of at most 30 strings")
        if not isinstance(platforms, list) or not platforms:
            raise ValueError("metadata platforms must be a non-empty list")
        normalized: list[str] = []
        for value in platforms:
            key = str(value).strip().lower()
            if key not in PLATFORM_ALIASES:
                raise ValueError(f"unsupported platform profile: {key}")
            platform = PLATFORM_ALIASES[key]
            if platform not in normalized:
                normalized.append(platform)
        payload["title"] = title
        payload["description"] = description
        payload["tags"] = tags
        payload["platforms"] = normalized
        payload.setdefault("privacy_status", "private")
        if payload["privacy_status"] not in {"private", "unlisted", "public"}:
            raise ValueError("privacy_status must be private, unlisted, or public")
        if not isinstance(payload.get("approved", False), bool):
            raise ValueError("approved must be a boolean")
        if not isinstance(payload.get("rights_cleared", False), bool):
            raise ValueError("rights_cleared must be a boolean")
        return payload

    def scan(self, *, now: Optional[float] = None) -> dict[str, Any]:
        """Discover stable MP4s, validate source/metadata, and persist per-platform jobs."""
        current = time.time() if now is None else now
        outcome: dict[str, Any] = {"discovered": 0, "queued": 0, "needs_review": 0, "errors": []}
        for path in sorted(self.config.inbox_dir.iterdir()):
            if path.suffix.lower() not in VIDEO_SUFFIXES or not path.is_file() or path.is_symlink():
                continue
            outcome["discovered"] += 1
            try:
                if path.stat().st_size > self.config.max_input_bytes:
                    raise ValueError(f"video exceeds {self.config.max_input_bytes} byte input limit")
                if not self._stable(path, current):
                    continue
                if not self._stable(path.with_suffix(".json"), current):
                    continue
                metadata = self._read_metadata(path)
                probe = self._probe_video(path)
                source_hash = _sha256_file(path, self.config.max_input_bytes)
                platform_metadata = metadata.get("platform_metadata") or {}
                if not isinstance(platform_metadata, dict):
                    raise ValueError("platform_metadata must be a JSON object")
                for platform in metadata["platforms"]:
                    effective = dict(metadata)
                    overrides = platform_metadata.get(platform) or {}
                    if not isinstance(overrides, dict):
                        raise ValueError(f"platform_metadata.{platform} must be a JSON object")
                    allowed_override_keys = {
                        "title", "description", "tags", "privacy_status",
                        "video_url", "share_to_feed",
                    }
                    unexpected = set(overrides) - allowed_override_keys
                    if unexpected:
                        raise ValueError(
                            f"platform_metadata.{platform} contains unsupported overrides: {sorted(unexpected)}"
                        )
                    effective.update(overrides)
                    if not str(effective.get("title") or "").strip() or len(str(effective["title"])) > 100:
                        raise ValueError(f"platform_metadata.{platform}.title must be 1–100 characters")
                    if len(str(effective.get("description") or "")) > 5000:
                        raise ValueError(f"platform_metadata.{platform}.description exceeds 5000 characters")
                    if not isinstance(effective.get("tags"), list) or any(
                        not isinstance(tag, str) for tag in effective["tags"]
                    ):
                        raise ValueError(f"platform_metadata.{platform}.tags must be a list of strings")
                    metadata_hash = _canonical_hash(effective)
                    review_reason = []
                    if not effective.get("approved", False):
                        review_reason.append("metadata approval is missing")
                    if not effective.get("rights_cleared", False):
                        review_reason.append("rights clearance is missing")
                    if (
                        effective.get("ai_generated") or effective.get("realistic_alteration")
                    ) and not effective.get("disclosure_reviewed"):
                        review_reason.append("AI/altered-media disclosure has not been reviewed")
                    if effective.get("privacy_status") == "public" and not self.config.allow_public:
                        review_reason.append("public publishing requires AIVF_FOLDER_ALLOW_PUBLIC=1")
                    if platform == "instagram":
                        from urllib.parse import urlparse
                        parsed_url = urlparse(str(effective.get("video_url") or "").strip())
                        if parsed_url.scheme != "https" or not parsed_url.hostname:
                            review_reason.append("Instagram requires platform_metadata.instagram.video_url to be a public HTTPS video URL")
                        if effective.get("privacy_status") != "public":
                            review_reason.append("Instagram Reels publishing requires public visibility")
                    if platform == "tiktok" and effective.get("privacy_status") == "unlisted":
                        review_reason.append("TikTok Direct Post does not support unlisted visibility")
                    needs_review = bool(review_reason)
                    platform_account_id = self.account_for(platform)
                    job_id = hashlib.sha256(
                        f"{platform}\0{platform_account_id}\0{source_hash}".encode("utf-8")
                    ).hexdigest()
                    if platform not in IMPLEMENTED_UPLOADERS:
                        state = "BLOCKED_UNSUPPORTED"
                        error = f"No direct authenticated uploader is implemented for {platform}"
                    elif needs_review:
                        state = "NEEDS_REVIEW"
                        error = "; ".join(review_reason)
                    else:
                        state = "PENDING"
                        error = None
                    with self._connect() as conn:
                        existing = conn.execute(
                            "SELECT * FROM publishing_jobs WHERE platform=? AND account_id=? AND source_sha256=?",
                            (platform, platform_account_id, source_hash),
                        ).fetchone()
                        if existing:
                            if existing["metadata_sha256"] != metadata_hash and existing["state"] != "PUBLISHED":
                                conn.execute(
                                    "UPDATE publishing_jobs SET state='NEEDS_REVIEW',last_error=?,updated_at=? WHERE id=?",
                                    ("metadata changed for an existing content identity; inspect manually to avoid duplicates", _utc_now(), existing["id"]),
                                )
                                outcome["needs_review"] += 1
                            elif existing["state"] not in {"PUBLISHED", "UPLOADING", "BLOCKED_UNSUPPORTED"}:
                                conn.execute(
                                    "UPDATE publishing_jobs SET source_path=?,metadata_json=?,updated_at=? WHERE id=?",
                                    (str(path.resolve()), json.dumps(effective, ensure_ascii=False), _utc_now(), existing["id"]),
                                )
                            continue
                        conn.execute(
                            "INSERT INTO publishing_jobs(id,platform,account_id,source_path,source_sha256,"
                            "metadata_sha256,metadata_json,state,last_error,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                job_id, platform, platform_account_id, str(path.resolve()), source_hash,
                                metadata_hash, json.dumps(effective, ensure_ascii=False), state, error,
                                _utc_now(), _utc_now(),
                            ),
                        )
                    outcome["queued"] += int(state == "PENDING")
                    outcome["needs_review"] += int(state == "NEEDS_REVIEW")
            except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
                outcome["errors"].append({"file": path.name, "error": str(exc)})
        return outcome

    def publish_one(self, *, now: Optional[float] = None) -> dict[str, Any]:
        """Publish at most one eligible job; call from a daily scheduled task."""
        current = time.time() if now is None else now
        with self._connect() as conn:
            # Take the SQLite write lock before selection so two invocations cannot
            # claim the same source job at once. Expired leases recover after a killed process.
            conn.execute("BEGIN IMMEDIATE")
            candidates = conn.execute(
                "SELECT * FROM publishing_jobs WHERE "
                "(state IN ('PENDING','RETRY_WAIT') OR "
                "(state='UPLOADING' AND (lease_until_epoch IS NULL OR lease_until_epoch<=?))) "
                "AND (next_attempt_epoch IS NULL OR next_attempt_epoch<=?) "
                "ORDER BY created_at,id",
                (current, current),
            ).fetchall()
            if not candidates:
                return {"status": "no_eligible_jobs"}
            day = datetime.fromtimestamp(current, timezone.utc).date().isoformat()
            job = None
            capped: list[dict[str, Any]] = []
            for candidate in candidates:
                candidate_platform = str(candidate["platform"])
                candidate_account_id = str(candidate["account_id"])
                published = conn.execute(
                    "SELECT COUNT(*) FROM publishing_jobs WHERE state='PUBLISHED' AND platform=? "
                    "AND account_id=? AND substr(published_at,1,10)=?",
                    (candidate_platform, candidate_account_id, day),
                ).fetchone()[0]
                if int(published) >= self.config.max_videos_per_day:
                    capped.append({
                        "platform": candidate_platform,
                        "account_id": candidate_account_id,
                        "published_today": int(published),
                    })
                    continue
                job = candidate
                break
            if job is None:
                return {"status": "daily_limit_reached", "capped_destinations": capped}
            platform = str(job["platform"])
            platform_account_id = str(job["account_id"])
            if platform not in IMPLEMENTED_UPLOADERS:
                return {"status": "blocked", "job_id": job["id"], "reason": "no uploader"}
            metadata = json.loads(job["metadata_json"])
            if metadata.get("privacy_status", "private") == "public" and not self.config.allow_public:
                conn.execute(
                    "UPDATE publishing_jobs SET state='NEEDS_REVIEW',last_error=?,updated_at=? WHERE id=?",
                    ("public publishing requires AIVF_FOLDER_ALLOW_PUBLIC=1", _utc_now(), job["id"]),
                )
                return {"status": "needs_review", "job_id": job["id"], "reason": "public publishing disabled"}
            attempts = int(job["attempts"]) + 1
            conn.execute(
                "UPDATE publishing_jobs SET state='UPLOADING',attempts=?,lease_until_epoch=?,"
                "last_error=NULL,updated_at=? WHERE id=?",
                (attempts, current + 3600.0, _utc_now(), job["id"]),
            )
        try:
            common = {
                "title": str(metadata["title"]),
                "description": str(metadata.get("description") or ""),
                "tags": metadata.get("tags") or [],
                "privacy_status": str(metadata.get("privacy_status") or "private"),
                "idempotency_key": f"folder-{platform_account_id}-{job['id']}",
                "idempotency_directory": str(self.config.state_dir / f"{platform}-idempotency"),
            }
            if platform == "youtube":
                from .youtube_publisher import upload_video
                result = upload_video(
                    job["source_path"],
                    **common,
                    client_secrets_path=self.config.client_secrets_path,
                    token_path=self.config.token_path,
                    ai_generated=bool(metadata.get("ai_generated", False)),
                    realistic_alteration=bool(metadata.get("realistic_alteration", False)),
                    disclosure_reviewed=bool(metadata.get("disclosure_reviewed", False)),
                )
            elif platform == "tiktok":
                from .tiktok_publisher import publish_video
                result = publish_video(job["source_path"], **common)
            elif platform == "instagram":
                from .instagram_publisher import publish_video
                result = publish_video(
                    job["source_path"],
                    **common,
                    video_url=str(metadata.get("video_url") or ""),
                    share_to_feed=bool(metadata.get("share_to_feed", True)),
                )
            else:
                raise RuntimeError(f"No direct publisher is implemented for {platform}")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:2000]
            # Ambiguous upload errors are retained and retried through the uploader's
            # durable idempotency journal, which refuses unsafe duplicate uploads.
            terminal = attempts >= 5 or any(
                marker in message.lower()
                for marker in ("outcome is uncertain", "refusing to risk a duplicate", "operator review is required")
            )
            state = "NEEDS_REVIEW" if terminal else "RETRY_WAIT"
            next_attempt = None if terminal else current + min(86400.0, 300.0 * (2 ** (attempts - 1)))
            with self._connect() as conn:
                conn.execute(
                    "UPDATE publishing_jobs SET state=?,next_attempt_epoch=?,lease_until_epoch=NULL,"
                    "last_error=?,updated_at=? WHERE id=?",
                    (state, next_attempt, message, _utc_now(), job["id"]),
                )
            return {"status": state.lower(), "job_id": job["id"], "error": message}
        remote_id = str(result.get("video_id") or "").strip()
        if not remote_id:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE publishing_jobs SET state='NEEDS_REVIEW',lease_until_epoch=NULL,"
                    "last_error=?,updated_at=? WHERE id=?",
                    ("uploader returned no verified remote video ID", _utc_now(), job["id"]),
                )
            return {"status": "needs_review", "job_id": job["id"], "reason": "missing remote ID"}
        published_at = _utc_now()
        remote_url = str(result["url"]) if result.get("url") else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE publishing_jobs SET state='PUBLISHED',remote_id=?,remote_url=?,published_at=?,"
                "next_attempt_epoch=NULL,lease_until_epoch=NULL,last_error=NULL,updated_at=? WHERE id=?",
                (remote_id, remote_url, published_at, published_at, job["id"]),
            )
        return {"status": "published", "job_id": job["id"], "remote_id": remote_id, "url": remote_url}

    def approve_job(self, job_id: str) -> dict[str, Any]:
        """Explicitly release a reviewed job after revalidating its current sidecar."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM publishing_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown publishing job: {job_id}")
        if row["state"] != "NEEDS_REVIEW":
            raise ValueError(f"job {job_id} is not awaiting review (state={row['state']})")
        platform = str(row["platform"])
        if platform not in IMPLEMENTED_UPLOADERS:
            raise ValueError(f"no direct uploader is implemented for {platform}")
        source = Path(row["source_path"])
        if not source.is_file() or source.is_symlink():
            raise ValueError("source file is missing or is not a regular file")
        now = time.time()
        if not self._stable(source, now):
            raise ValueError("source file has not passed the stable-file check")
        metadata = self._read_metadata(source)
        normalized_platforms = {
            PLATFORM_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
            for value in metadata["platforms"]
        }
        if platform not in normalized_platforms:
            raise ValueError("current sidecar no longer includes this destination platform")
        self._probe_video(source)
        source_hash = _sha256_file(source, self.config.max_input_bytes)
        if source_hash != row["source_sha256"]:
            raise ValueError("source content changed; refusing to reuse the old publishing job")
        effective = dict(metadata)
        overrides = (metadata.get("platform_metadata") or {}).get(platform) or {}
        allowed_override_keys = {
            "title", "description", "tags", "privacy_status", "video_url", "share_to_feed"
        }
        if not isinstance(overrides, dict) or set(overrides) - allowed_override_keys:
            raise ValueError("invalid platform-specific metadata")
        effective.update(overrides)
        if not effective.get("approved", False) or not effective.get("rights_cleared", False):
            raise ValueError("sidecar must set approved=true and rights_cleared=true")
        if (effective.get("ai_generated") or effective.get("realistic_alteration")) and not effective.get("disclosure_reviewed"):
            raise ValueError("AI/altered-media disclosure must be reviewed before approval")
        if effective.get("privacy_status") == "public" and not self.config.allow_public:
            raise ValueError("set AIVF_FOLDER_ALLOW_PUBLIC=1 explicitly before approving public publication")
        if platform == "instagram":
            from urllib.parse import urlparse
            parsed_url = urlparse(str(effective.get("video_url") or "").strip())
            if parsed_url.scheme != "https" or not parsed_url.hostname:
                raise ValueError("Instagram requires a public HTTPS video_url in its sidecar")
            if effective.get("privacy_status") != "public":
                raise ValueError("Instagram Reels publishing requires public visibility")
        if platform == "tiktok" and effective.get("privacy_status") == "unlisted":
            raise ValueError("TikTok Direct Post does not support unlisted visibility")
        if not str(effective.get("title") or "").strip() or len(str(effective["title"])) > 100:
            raise ValueError("title must be 1–100 characters")
        if len(str(effective.get("description") or "")) > 5000:
            raise ValueError("description exceeds 5000 characters")
        if not isinstance(effective.get("tags"), list) or any(not isinstance(tag, str) for tag in effective["tags"]):
            raise ValueError("tags must be a list of strings")
        with self._connect() as conn:
            conn.execute(
                "UPDATE publishing_jobs SET metadata_sha256=?,metadata_json=?,state='PENDING',"
                "attempts=0,next_attempt_epoch=NULL,last_error=NULL,updated_at=? WHERE id=? AND state='NEEDS_REVIEW'",
                (_canonical_hash(effective), json.dumps(effective, ensure_ascii=False), _utc_now(), job_id),
            )
        return {"status": "approved_for_queue", "job_id": job_id, "platform": platform}

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,platform,account_id,source_path,source_sha256,state,attempts,remote_id,"
                "remote_url,last_error,created_at,updated_at,published_at "
                "FROM publishing_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely publish existing MP4 files from a watched folder (YouTube uploader only)."
    )
    parser.add_argument("command", choices=("scan", "run-once", "list", "watch", "approve"))
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between scans in watch mode")
    parser.add_argument("--limit", type=int, default=100, help="maximum queue rows to print in list mode")
    parser.add_argument("--job-id", default=None, help="publishing job ID for approve mode")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=os.environ.get("AIVF_LOG_LEVEL", "INFO"))
    config = FolderPublisherConfig.from_environment()
    publisher = FolderPublisher(config)
    if args.command == "approve":
        if not args.job_id:
            raise SystemExit("approve requires --job-id")
        print(json.dumps(publisher.approve_job(args.job_id), indent=2))
        return 0
    if args.command == "list":
        print(json.dumps(publisher.list_jobs(args.limit), indent=2, ensure_ascii=False))
        return 0
    if args.command == "scan":
        print(json.dumps(publisher.scan(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "run-once":
        scan = publisher.scan()
        print(json.dumps({"scan": scan, "publish": publisher.publish_one()}, indent=2, ensure_ascii=False))
        return 0
    if args.interval < 5:
        raise SystemExit("--interval must be at least 5 seconds in watch mode")
    while True:
        scan = publisher.scan()
        publish = publisher.publish_one()
        logger.info("folder scan=%s publish=%s", scan, publish)
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
