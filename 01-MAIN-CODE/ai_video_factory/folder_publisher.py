"""Opt-in, durable watcher for publishing existing videos from a local folder.

Only stable, ffprobe-valid files with an explicit sidecar approval and rights
attestation enter the upload queue. Uploads default to private and remain
disabled until AIVF_FOLDER_PUBLISH=1 or the --publish flag is supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .youtube_publisher import upload_video

LOG = logging.getLogger(__name__)
SCHEMA_VERSION = 2
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm"}
TEMP_SUFFIXES = {".part", ".partial", ".tmp", ".crdownload"}
TERMINAL_STATES = {"PUBLISHED", "DUPLICATE", "SOURCE_CHANGED"}


@dataclass(frozen=True)
class FolderPublisherConfig:
    watch_dir: Path
    db_path: Path
    settle_seconds: int = 30
    poll_seconds: int = 10
    max_videos_per_day: int = 2
    max_file_bytes: int = 8 * 1024 * 1024 * 1024
    max_staged_bytes: int = 20 * 1024 * 1024 * 1024
    max_duration_seconds: int = 2 * 60 * 60
    publish_enabled: bool = False
    privacy_status: str = "private"
    allow_public: bool = False
    client_secrets_path: Optional[str] = None
    token_path: Optional[str] = None

    @classmethod
    def from_environment(cls) -> "FolderPublisherConfig":
        state_dir = Path(os.environ.get("AIVF_STATE_DIR", "./state")).expanduser()
        return cls(
            watch_dir=Path(os.environ.get("AIVF_INCOMING_DIR", "./incoming")).expanduser(),
            db_path=Path(
                os.environ.get(
                    "AIVF_FOLDER_STATE_DB",
                    str(state_dir / "folder-publisher.sqlite"),
                )
            ).expanduser(),
            settle_seconds=max(0, int(os.environ.get("AIVF_FOLDER_SETTLE_SECONDS", "30"))),
            poll_seconds=max(1, int(os.environ.get("AIVF_FOLDER_POLL_SECONDS", "10"))),
            max_videos_per_day=max(0, int(os.environ.get("AIVF_FOLDER_MAX_VIDEOS_PER_DAY", "2"))),
            max_file_bytes=max(1, int(os.environ.get("AIVF_FOLDER_MAX_FILE_BYTES", str(8 * 1024**3)))),
            max_staged_bytes=max(1, int(os.environ.get("AIVF_FOLDER_MAX_STAGED_BYTES", str(20 * 1024**3)))),
            max_duration_seconds=max(1, int(os.environ.get("AIVF_FOLDER_MAX_DURATION_SECONDS", "7200"))),
            publish_enabled=os.environ.get("AIVF_FOLDER_PUBLISH", "0").strip() == "1",
            privacy_status=os.environ.get("AIVF_FOLDER_PRIVACY", "private").strip().lower(),
            allow_public=os.environ.get("AIVF_FOLDER_ALLOW_PUBLIC", "0").strip() == "1",
            client_secrets_path=os.environ.get("YOUTUBE_CLIENT_SECRETS"),
            token_path=os.environ.get("YOUTUBE_TOKEN_PATH"),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_video(path: Path, *, max_duration_seconds: int) -> dict[str, Any]:
    """Reject corrupt/non-video inputs before any upload is attempted."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration,format_name:stream=codec_type,codec_name,width,height,avg_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=25,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError(f"ffprobe could not validate this file: {exc}") from exc

    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ValueError("ffprobe did not return a stream list")
    videos = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"]
    if not videos:
        raise ValueError("file contains no video stream")
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    try:
        duration = float(fmt.get("duration"))
        width = int(videos[0].get("width"))
        height = int(videos[0].get("height"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("video duration or dimensions are missing/invalid") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("video duration must be a finite positive number")
    if duration > max_duration_seconds:
        raise ValueError(f"video duration exceeds the configured {max_duration_seconds}-second limit")
    if width < 1 or height < 1 or width > 16384 or height > 16384:
        raise ValueError("video dimensions are invalid or exceed the 16384-pixel safety limit")
    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "video_codec": str(videos[0].get("codec_name") or ""),
        "format_name": str(fmt.get("format_name") or ""),
        "audio_streams": sum(
            1 for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"
        ),
    }


class FolderQueue:
    """SQLite-backed queue with safe discovery and upload-lease recovery."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"folder publisher database schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version == 0:
                conn.executescript(
                    """
                    CREATE TABLE folder_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        path TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        stable_since REAL NOT NULL,
                        source_sha256 TEXT,
                        staged_path TEXT,
                        probe_json TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        sidecar_mtime_ns INTEGER,
                        state TEXT NOT NULL,
                        discovered_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at REAL NOT NULL DEFAULT 0,
                        lease_owner TEXT,
                        lease_until REAL,
                        video_id TEXT,
                        video_url TEXT,
                        published_at REAL,
                        duplicate_of INTEGER,
                        last_error TEXT NOT NULL DEFAULT ''
                    );
                    CREATE INDEX folder_items_state_due
                        ON folder_items(state, next_attempt_at, discovered_at);
                    CREATE INDEX folder_items_hash ON folder_items(source_sha256);
                    CREATE INDEX folder_items_path ON folder_items(path, id);
                    PRAGMA user_version=2;
                    """
                )
            elif version == 1:
                # Explicit one-time migration for databases created by the first
                # unreleased folder-publisher revision. Rebuild rather than leave
                # its old UNIQUE(path, source_sha256) constraint in place.
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.executescript(
                    """
                    CREATE TABLE folder_items_v2 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        path TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        stable_since REAL NOT NULL,
                        source_sha256 TEXT,
                        staged_path TEXT,
                        probe_json TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        sidecar_mtime_ns INTEGER,
                        state TEXT NOT NULL,
                        discovered_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        next_attempt_at REAL NOT NULL DEFAULT 0,
                        lease_owner TEXT,
                        lease_until REAL,
                        video_id TEXT,
                        video_url TEXT,
                        published_at REAL,
                        duplicate_of INTEGER,
                        last_error TEXT NOT NULL DEFAULT ''
                    );
                    INSERT INTO folder_items_v2
                        (id,path,size_bytes,mtime_ns,stable_since,source_sha256,staged_path,
                         probe_json,metadata_json,sidecar_mtime_ns,state,discovered_at,updated_at,
                         attempts,next_attempt_at,lease_owner,lease_until,video_id,video_url,
                         published_at,duplicate_of,last_error)
                    SELECT id,path,size_bytes,mtime_ns,stable_since,source_sha256,NULL,
                           probe_json,metadata_json,sidecar_mtime_ns,state,discovered_at,updated_at,
                           attempts,next_attempt_at,lease_owner,lease_until,video_id,video_url,
                           published_at,duplicate_of,last_error
                    FROM folder_items;
                    DROP TABLE folder_items;
                    ALTER TABLE folder_items_v2 RENAME TO folder_items;
                    CREATE INDEX folder_items_state_due
                        ON folder_items(state, next_attempt_at, discovered_at);
                    CREATE INDEX folder_items_hash ON folder_items(source_sha256);
                    CREATE INDEX folder_items_path ON folder_items(path, id);
                    PRAGMA user_version=2;
                    """
                )
                conn.execute("PRAGMA foreign_keys=ON")
            elif version == SCHEMA_VERSION:
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='folder_items'"
                ).fetchone():
                    raise RuntimeError("folder publisher database is marked migrated but required table is missing")
            else:
                raise RuntimeError(f"unsupported folder publisher schema version: {version}")

    def items(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM folder_items ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT state, COUNT(*) AS count FROM folder_items GROUP BY state"
            ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    def get_item(self, item_id: int) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM folder_items WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row is not None else None

    def set_staged_path(self, item_id: int, path: Path, *, now: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE folder_items SET staged_path=?, updated_at=? WHERE id=? "
                "AND state NOT IN ('UPLOADING','PUBLISHED','DUPLICATE','SOURCE_CHANGED')",
                (str(path), now, item_id),
            )

    def observe(self, path: Path, *, now: float) -> dict[str, Any]:
        stat_result = path.stat()
        canonical_path = str(path.resolve(strict=True))
        with self._connect() as conn:
            current = conn.execute(
                "SELECT * FROM folder_items WHERE path=? ORDER BY id DESC LIMIT 1",
                (canonical_path,),
            ).fetchone()
            if current is None or (
                int(current["size_bytes"]) != stat_result.st_size
                or int(current["mtime_ns"]) != stat_result.st_mtime_ns
            ):
                if current is not None and current["state"] not in TERMINAL_STATES:
                    conn.execute(
                        "UPDATE folder_items SET state='SOURCE_CHANGED', updated_at=?, "
                        "last_error='source changed after its previous observation', "
                        "lease_owner=NULL, lease_until=NULL WHERE id=?",
                        (now, int(current["id"])),
                    )
                conn.execute(
                    """
                    INSERT INTO folder_items
                        (path,size_bytes,mtime_ns,stable_since,state,discovered_at,updated_at)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        canonical_path,
                        stat_result.st_size,
                        stat_result.st_mtime_ns,
                        now,
                        "WAITING_STABLE",
                        now,
                        now,
                    ),
                )
                current = conn.execute("SELECT * FROM folder_items WHERE id=last_insert_rowid()").fetchone()
            return dict(current)

    def update_validation(
        self,
        item_id: int,
        *,
        source_hash: str,
        probe: Optional[dict[str, Any]],
        now: float,
        error: str = "",
        retry_after_seconds: int = 0,
    ) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM folder_items WHERE id=?", (item_id,)).fetchone()
            if current is None:
                conn.rollback()
                raise KeyError(item_id)
            duplicate = conn.execute(
                "SELECT id,state FROM folder_items WHERE source_sha256=? AND id<>? "
                "AND state IN ('READY','UPLOADING','PUBLISHED') ORDER BY id LIMIT 1",
                (source_hash, item_id),
            ).fetchone()
            if duplicate:
                conn.execute(
                    "UPDATE folder_items SET source_sha256=?, state='DUPLICATE', duplicate_of=?, "
                    "updated_at=?, last_error='identical content is already queued or published' WHERE id=?",
                    (source_hash, int(duplicate["id"]), now, item_id),
                )
            else:
                next_state = "NEEDS_REVIEW" if error else str(current["state"])
                conn.execute(
                    "UPDATE folder_items SET source_sha256=?, probe_json=?, state=?, updated_at=?, "
                    "next_attempt_at=?, last_error=? WHERE id=?",
                    (
                        source_hash or None,
                        json.dumps(probe, sort_keys=True) if probe is not None else None,
                        next_state,
                        now,
                        now + max(0, retry_after_seconds),
                        error,
                        item_id,
                    ),
                )
            conn.commit()

    def update_review(
        self,
        item_id: int,
        *,
        metadata: dict[str, Any],
        sidecar_mtime_ns: Optional[int],
        ready: bool,
        reason: str,
        now: float,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT state FROM folder_items WHERE id=?", (item_id,)).fetchone()
            if row is None:
                raise KeyError(item_id)
            if row["state"] in {"UPLOADING", "PUBLISHED", "DUPLICATE", "SOURCE_CHANGED"}:
                return
            conn.execute(
                "UPDATE folder_items SET metadata_json=?, sidecar_mtime_ns=?, state=?, "
                "updated_at=?, last_error=? WHERE id=?",
                (
                    json.dumps(metadata, sort_keys=True),
                    sidecar_mtime_ns,
                    "READY" if ready else "NEEDS_REVIEW",
                    now,
                    "" if ready else reason[:2000],
                    item_id,
                ),
            )

    def claim_next(self, *, max_per_day: int, now: float) -> Optional[dict[str, Any]]:
        if max_per_day < 1:
            return None
        day = datetime.fromtimestamp(now, tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        owner = uuid.uuid4().hex
        # The long lease prevents a second daemon from racing a slow resumable
        # upload. Stale UPLOADING entries are reclaimed only after process loss.
        lease_until = now + 2 * 60 * 60
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            sent_today = int(
                conn.execute(
                    "SELECT COUNT(*) FROM folder_items WHERE state='PUBLISHED' AND published_at>=?",
                    (day,),
                ).fetchone()[0]
            )
            if sent_today >= max_per_day:
                conn.commit()
                return None
            row = conn.execute(
                "SELECT * FROM folder_items WHERE "
                "(state='READY' AND next_attempt_at<=?) OR "
                "(state='UPLOADING' AND COALESCE(lease_until,0)<=? AND next_attempt_at<=?) "
                "ORDER BY CASE state WHEN 'UPLOADING' THEN 0 ELSE 1 END, discovered_at, id LIMIT 1",
                (now, now, now),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                "UPDATE folder_items SET state='UPLOADING', attempts=attempts+1, "
                "lease_owner=?, lease_until=?, updated_at=?, last_error='' WHERE id=?",
                (owner, lease_until, now, int(row["id"])),
            )
            result = conn.execute("SELECT * FROM folder_items WHERE id=?", (int(row["id"]),)).fetchone()
            conn.commit()
        item = dict(result)
        item["lease_owner"] = owner
        return item

    def mark_published(self, item_id: int, owner: str, result: dict[str, Any], *, now: float) -> None:
        video_id = str(result.get("video_id") or "").strip()
        if not video_id:
            raise RuntimeError("publisher returned no remote video ID")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE folder_items SET state='PUBLISHED', video_id=?, video_url=?, published_at=?, "
                "updated_at=?, lease_owner=NULL, lease_until=NULL, next_attempt_at=0, last_error='' "
                "WHERE id=? AND state='UPLOADING' AND lease_owner=?",
                (video_id, str(result.get("url") or ""), now, now, item_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("upload lease was lost; remote upload is recorded but queue state was not changed")

    def defer_uncertain(self, item_id: int, owner: str, error: str, *, now: float, attempts: int) -> None:
        # Never mark an ambiguous remote upload as a clean failure: retrying must
        # enter upload_video with the same idempotency key to reconcile first.
        delay = min(3600, max(600, 60 * (2 ** min(max(attempts - 1, 0), 6))))
        with self._connect() as conn:
            conn.execute(
                "UPDATE folder_items SET state='UPLOADING', lease_owner=NULL, lease_until=NULL, "
                "next_attempt_at=?, updated_at=?, last_error=? "
                "WHERE id=? AND state='UPLOADING' AND lease_owner=?",
                (now + delay, now, error[:2000], item_id, owner),
            )


class FolderPublisher:
    def __init__(self, config: FolderPublisherConfig) -> None:
        self.config = config
        self.watch_dir = config.watch_dir.expanduser().resolve()
        self.db_path = config.db_path.expanduser().resolve()
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.store = FolderQueue(self.db_path)
        if config.privacy_status not in {"private", "unlisted", "public"}:
            raise ValueError("privacy status must be private, unlisted or public")
        if config.privacy_status == "public" and not config.allow_public:
            raise ValueError("public upload is blocked unless AIVF_FOLDER_ALLOW_PUBLIC=1 is set")

    def _is_within_root(self, path: Path) -> bool:
        try:
            path.resolve(strict=True).relative_to(self.watch_dir)
            return True
        except (OSError, ValueError):
            return False

    def _candidates(self) -> Iterable[Path]:
        for candidate in self.watch_dir.rglob("*"):
            if candidate.name.startswith(".") or candidate.suffix.lower() in TEMP_SUFFIXES:
                continue
            if candidate.suffix.lower() not in VIDEO_EXTENSIONS or candidate.is_symlink():
                continue
            if not self._is_within_root(candidate) or not candidate.is_file():
                continue
            yield candidate

    def _sidecar(self, video: Path) -> Path:
        return Path(str(video) + ".publish.json")

    def _load_metadata(self, video: Path) -> tuple[dict[str, Any], Optional[int], bool, str]:
        sidecar = self._sidecar(video)
        default = {
            "title": video.stem[:100],
            "description": "",
            "tags": [],
            "privacy_status": self.config.privacy_status,
            "category_id": "22",
            "ai_generated": None,
            "realistic_alteration": None,
            "disclosure_reviewed": False,
            "rights_reviewed": False,
            "publish_approved": False,
            "thumbnail_path": "",
            "caption_path": "",
            "caption_language": "en",
        }
        if not sidecar.is_file():
            return default, None, False, "sidecar missing; use the approve command after reviewing rights and disclosure"
        try:
            raw = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return default, None, False, f"sidecar cannot be read as JSON: {exc}"
        if not isinstance(raw, dict):
            return default, sidecar.stat().st_mtime_ns, False, "sidecar root must be a JSON object"
        metadata = {**default, **raw}
        title = str(metadata.get("title") or "").strip()
        description = str(metadata.get("description") or "")
        tags = metadata.get("tags", [])
        if not title or len(title) > 100:
            return metadata, sidecar.stat().st_mtime_ns, False, "title must contain 1-100 characters"
        if len(description) > 5000:
            return metadata, sidecar.stat().st_mtime_ns, False, "description exceeds 5000 characters"
        if not isinstance(tags, list) or len(tags) > 30 or any(
            not isinstance(tag, str) or not tag.strip() or len(tag) > 500 for tag in tags
        ):
            return metadata, sidecar.stat().st_mtime_ns, False, "tags must be a list of at most 30 non-empty strings"
        privacy = str(metadata.get("privacy_status") or self.config.privacy_status).lower()
        if privacy not in {"private", "unlisted", "public"}:
            return metadata, sidecar.stat().st_mtime_ns, False, "privacy_status must be private, unlisted or public"
        if privacy == "public" and not self.config.allow_public:
            return metadata, sidecar.stat().st_mtime_ns, False, "public upload blocked; enable the explicit public-upload opt-in"
        metadata["privacy_status"] = privacy
        for field in ("ai_generated", "realistic_alteration", "disclosure_reviewed", "rights_reviewed", "publish_approved"):
            if not isinstance(metadata.get(field), bool):
                return metadata, sidecar.stat().st_mtime_ns, False, f"{field} must be explicitly set to a JSON boolean"
        if not metadata["publish_approved"]:
            return metadata, sidecar.stat().st_mtime_ns, False, "publish_approved must be true after human review"
        if not metadata["rights_reviewed"]:
            return metadata, sidecar.stat().st_mtime_ns, False, "rights_reviewed must be true after checking permission to use the media"
        if (metadata["ai_generated"] or metadata["realistic_alteration"]) and not metadata["disclosure_reviewed"]:
            return metadata, sidecar.stat().st_mtime_ns, False, "AI/altered-content disclosure review is required"
        for field in ("thumbnail_path", "caption_path"):
            raw_asset = str(metadata.get(field) or "").strip()
            if not raw_asset:
                metadata[field] = ""
                continue
            asset = (video.parent / raw_asset).resolve()
            if not self._is_within_root(asset) or not asset.is_file():
                return metadata, sidecar.stat().st_mtime_ns, False, f"{field} must point to an existing file inside the watched folder"
            metadata[field] = str(asset.relative_to(video.parent.resolve()))
        metadata["title"] = title
        metadata["description"] = description
        metadata["tags"] = [tag.strip() for tag in tags]
        metadata["category_id"] = str(metadata.get("category_id") or "22")
        metadata["caption_language"] = str(metadata.get("caption_language") or "en")
        return metadata, sidecar.stat().st_mtime_ns, True, ""

    def scan(self) -> dict[str, Any]:
        now = time.time()
        seen = 0
        for path in self._candidates():
            try:
                observed = self.store.observe(path, now=now)
                seen += 1
                if observed["state"] in TERMINAL_STATES or observed["state"] == "UPLOADING":
                    continue
                if (
                    observed["state"] == "NEEDS_REVIEW"
                    and not observed.get("probe_json")
                    and float(observed.get("next_attempt_at") or 0) > now
                ):
                    continue
                if now - float(observed["stable_since"]) < self.config.settle_seconds:
                    continue
                source = Path(str(observed["path"]))
                try:
                    before = source.stat()
                except OSError as exc:
                    continue
                if before.st_size > self.config.max_file_bytes:
                    self.store.update_validation(
                        int(observed["id"]),
                        source_hash=str(observed.get("source_sha256") or ""),
                        probe=None,
                        now=now,
                        error=f"file exceeds configured maximum size of {self.config.max_file_bytes} bytes",
                        retry_after_seconds=300,
                    )
                    continue
                file_hash = str(observed.get("source_sha256") or "")
                probe: Optional[dict[str, Any]] = None
                if not file_hash:
                    file_hash = _sha256(source)
                probe_json = observed.get("probe_json")
                probe_valid = False
                if probe_json:
                    try:
                        cached_probe = json.loads(str(probe_json))
                        probe_valid = isinstance(cached_probe, dict) and float(cached_probe.get("duration_seconds", 0)) > 0
                        if probe_valid:
                            probe = cached_probe
                    except (ValueError, TypeError, json.JSONDecodeError):
                        probe_valid = False
                if not probe_valid:
                    try:
                        probe = probe_video(source, max_duration_seconds=self.config.max_duration_seconds)
                    except ValueError as exc:
                        self.store.update_validation(
                            int(observed["id"]),
                            source_hash=file_hash,
                            probe=None,
                            now=now,
                            error=str(exc),
                            retry_after_seconds=300,
                        )
                        continue
                after = source.stat()
                if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                    with self.store._connect() as conn:
                        conn.execute(
                            "UPDATE folder_items SET size_bytes=?,mtime_ns=?,stable_since=?,source_sha256=NULL,"
                            "probe_json=NULL,state='WAITING_STABLE',updated_at=?,last_error='file changed while it was being validated' WHERE id=?",
                            (after.st_size, after.st_mtime_ns, now, now, int(observed["id"])),
                        )
                    continue
                self.store.update_validation(
                    int(observed["id"]),
                    source_hash=file_hash,
                    probe=probe,
                    now=now,
                    error="",
                )
                refreshed = self.store.get_item(int(observed["id"]))
                if refreshed is None or refreshed["state"] == "DUPLICATE":
                    continue
                metadata, sidecar_mtime, ready, reason = self._load_metadata(source)
                # A source already being uploaded or successfully published is immutable
                # from the watcher’s point of view; never change its outcome from a sidecar edit.
                self.store.update_review(
                    int(observed["id"]),
                    metadata=metadata,
                    sidecar_mtime_ns=sidecar_mtime,
                    ready=ready,
                    reason=reason,
                    now=now,
                )
            except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
                LOG.warning("folder scan skipped %s: %s", path, exc)
        return {"observed_files": seen, "counts": self.store.counts()}

    def publish_one(self) -> Optional[dict[str, Any]]:
        item = self.store.claim_next(max_per_day=self.config.max_videos_per_day, now=time.time())
        if item is None:
            return None
        path = Path(str(item["path"]))
        metadata = json.loads(str(item["metadata_json"]))
        if not path.is_file() or not self._is_within_root(path):
            self.store.defer_uncertain(
                int(item["id"]), str(item["lease_owner"]), "source file disappeared or escaped the watch root",
                now=time.time(), attempts=int(item["attempts"]),
            )
            return {"id": item["id"], "state": "UPLOADING", "error": "source file unavailable"}
        source_hash = str(item.get("source_sha256") or "")
        if not source_hash:
            self.store.defer_uncertain(
                int(item["id"]), str(item["lease_owner"]), "validated source SHA-256 is missing",
                now=time.time(), attempts=int(item["attempts"]),
            )
            return {"id": item["id"], "state": "UPLOADING", "error": "source hash missing"}
        token_path = self.config.token_path or os.path.expanduser(
            "~/.config/edit-factory/youtube-token.json"
        )
        account_key = hashlib.sha256(
            (str(self.config.client_secrets_path or os.environ.get("YOUTUBE_CLIENT_SECRETS", "")) + "\0" + token_path).encode("utf-8")
        ).hexdigest()[:16]
        idempotency_key = f"folder-v1-{account_key}-{source_hash}"
        def asset_path(field: str) -> Optional[str]:
            raw = str(metadata.get(field) or "").strip()
            if not raw:
                return None
            target = (path.parent / raw).resolve()
            if not self._is_within_root(target) or not target.is_file():
                raise ValueError(f"{field} disappeared or is outside the watched folder")
            return str(target)
        try:
            response = upload_video(
                str(path),
                title=str(metadata["title"]),
                description=str(metadata.get("description") or ""),
                tags=metadata.get("tags") or [],
                privacy_status=str(metadata.get("privacy_status") or self.config.privacy_status),
                category_id=str(metadata.get("category_id") or "22"),
                thumbnail_path=asset_path("thumbnail_path"),
                caption_path=asset_path("caption_path"),
                caption_language=str(metadata.get("caption_language") or "en"),
                client_secrets_path=self.config.client_secrets_path,
                token_path=self.config.token_path,
                ai_generated=metadata.get("ai_generated") is True,
                realistic_alteration=metadata.get("realistic_alteration") is True,
                disclosure_reviewed=metadata.get("disclosure_reviewed") is True,
                idempotency_key=idempotency_key,
            )
            now = time.time()
            self.store.mark_published(
                int(item["id"]), str(item["lease_owner"]), response, now=now
            )
            return {
                "id": int(item["id"]),
                "state": "PUBLISHED",
                "video_id": str(response["video_id"]),
                "url": str(response.get("url") or ""),
                "deduplicated": bool(response.get("deduplicated", False)),
            }
        except Exception as exc:
            now = time.time()
            self.store.defer_uncertain(
                int(item["id"]),
                str(item["lease_owner"]),
                str(exc),
                now=now,
                attempts=int(item["attempts"]),
            )
            LOG.warning("publish attempt for item %s is deferred safely: %s", item["id"], exc)
            return {"id": int(item["id"]), "state": "UPLOADING", "error": str(exc)}

    def run_forever(self) -> None:
        LOG.info("watching %s; database=%s; uploads_enabled=%s", self.watch_dir, self.db_path, self.config.publish_enabled)
        while True:
            result = self.scan()
            LOG.info("folder scan: %s", json.dumps(result, sort_keys=True))
            if self.config.publish_enabled:
                result = self.publish_one()
                if result:
                    LOG.info("folder publish: %s", json.dumps(result, sort_keys=True))
            time.sleep(max(1, self.config.poll_seconds))


def approve_sidecar(
    watch_dir: str | Path,
    file_path: str | Path,
    *,
    acknowledge_disclosure: bool = False,
    ai_generated: Optional[bool] = None,
    realistic_alteration: Optional[bool] = None,
) -> Path:
    """Write an explicit approval/rights attestation sidecar for one watched file."""
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
    if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("approval target must be a regular supported video file inside the watched folder")
    sidecar = Path(str(candidate) + ".publish.json")
    if sidecar.is_symlink():
        raise ValueError("approval sidecar must not be a symbolic link")
    payload: dict[str, Any] = {}
    if sidecar.is_file():
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("existing sidecar must contain a JSON object")
        payload = raw
    payload.setdefault("title", candidate.stem[:100])
    payload.setdefault("description", "")
    payload.setdefault("tags", [])
    payload.setdefault("privacy_status", "private")
    payload.setdefault("category_id", "22")
    payload.setdefault("ai_generated", None)
    payload.setdefault("realistic_alteration", None)
    if ai_generated is not None:
        payload["ai_generated"] = ai_generated
    if realistic_alteration is not None:
        payload["realistic_alteration"] = realistic_alteration
    payload["publish_approved"] = True
    payload["rights_reviewed"] = True
    if acknowledge_disclosure:
        payload["disclosure_reviewed"] = True
    else:
        payload.setdefault("disclosure_reviewed", False)
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    temporary = sidecar.with_name(sidecar.name + f".{uuid.uuid4().hex}.partial")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, sidecar)
    return sidecar


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Safely publish existing videos from a local folder to YouTube")
    parser.add_argument("--watch-dir", default=os.environ.get("AIVF_INCOMING_DIR", "./incoming"))
    parser.add_argument("--db", default=os.environ.get("AIVF_FOLDER_STATE_DB", "./state/folder-publisher.sqlite"))
    parser.add_argument("--settle-seconds", type=int, default=int(os.environ.get("AIVF_FOLDER_SETTLE_SECONDS", "30")))
    parser.add_argument("--poll-seconds", type=int, default=int(os.environ.get("AIVF_FOLDER_POLL_SECONDS", "10")))
    parser.add_argument("--max-per-day", type=int, default=int(os.environ.get("AIVF_FOLDER_MAX_VIDEOS_PER_DAY", "2")))
    parser.add_argument("--max-file-bytes", type=int, default=int(os.environ.get("AIVF_FOLDER_MAX_FILE_BYTES", str(8 * 1024**3))))
    parser.add_argument("--max-duration-seconds", type=int, default=int(os.environ.get("AIVF_FOLDER_MAX_DURATION_SECONDS", "7200")))
    parser.add_argument("--privacy", choices=("private", "unlisted", "public"), default=os.environ.get("AIVF_FOLDER_PRIVACY", "private"))
    publish = parser.add_mutually_exclusive_group()
    publish.add_argument("--publish", dest="publish_enabled", action="store_true", help="enable real YouTube uploads")
    publish.add_argument("--dry-run", dest="publish_enabled", action="store_false", help="scan and queue files without uploading")
    parser.set_defaults(publish_enabled=os.environ.get("AIVF_FOLDER_PUBLISH", "0").strip() == "1")
    parser.add_argument("--allow-public", action="store_true", default=os.environ.get("AIVF_FOLDER_ALLOW_PUBLIC", "0").strip() == "1")
    parser.add_argument("--client-secrets", default=os.environ.get("YOUTUBE_CLIENT_SECRETS"))
    parser.add_argument("--token", default=os.environ.get("YOUTUBE_TOKEN_PATH"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="scan stable files and refresh the durable queue")
    sub.add_parser("status", help="show the durable queue")
    once = sub.add_parser("run-once", help="scan once and optionally upload one approved video")
    once.add_argument("--limit", type=int, default=50)
    daemon = sub.add_parser("daemon", help="continuously watch the folder")
    daemon.add_argument("--limit", type=int, default=50)
    approve = sub.add_parser("approve", help="attest that a video is approved and its rights are cleared")
    approve.add_argument("file")
    approve.add_argument("--acknowledge-disclosure", action="store_true")
    ai_flags = approve.add_mutually_exclusive_group()
    ai_flags.add_argument("--ai-generated", dest="ai_generated", action="store_true")
    ai_flags.add_argument("--not-ai-generated", dest="ai_generated", action="store_false")
    altered_flags = approve.add_mutually_exclusive_group()
    altered_flags.add_argument("--realistic-alteration", dest="realistic_alteration", action="store_true")
    altered_flags.add_argument("--no-realistic-alteration", dest="realistic_alteration", action="store_false")
    approve.set_defaults(ai_generated=None, realistic_alteration=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=os.environ.get("AIVF_LOG_LEVEL", "INFO"))

    if args.command == "approve":
        sidecar = approve_sidecar(
            args.watch_dir,
            args.file,
            acknowledge_disclosure=bool(args.acknowledge_disclosure),
            ai_generated=args.ai_generated,
            realistic_alteration=args.realistic_alteration,
        )
        print(json.dumps({"sidecar": str(sidecar), "publish_approved": True, "rights_reviewed": True}, indent=2))
        return 0

    config = FolderPublisherConfig(
        watch_dir=Path(args.watch_dir),
        db_path=Path(args.db),
        settle_seconds=max(0, args.settle_seconds),
        poll_seconds=max(1, args.poll_seconds),
        max_videos_per_day=max(0, args.max_per_day),
        max_file_bytes=max(1, args.max_file_bytes),
        max_duration_seconds=max(1, args.max_duration_seconds),
        publish_enabled=bool(args.publish_enabled),
        privacy_status=str(args.privacy),
        allow_public=bool(args.allow_public),
        client_secrets_path=args.client_secrets,
        token_path=args.token,
    )
    publisher = FolderPublisher(config)
    if args.command == "scan":
        print(json.dumps(publisher.scan(), indent=2, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps({"counts": publisher.store.counts(), "items": publisher.store.items()}, indent=2, sort_keys=True))
        return 0
    if args.command == "run-once":
        scan = publisher.scan()
        uploaded = publisher.publish_one() if config.publish_enabled else None
        print(json.dumps({"scan": scan, "publish": uploaded, "counts": publisher.store.counts()}, indent=2, sort_keys=True))
        return 0
    if args.command == "daemon":
        publisher.run_forever()
        return 0
    return 2


__all__ = ["FolderPublisherConfig", "FolderQueue", "FolderPublisher", "approve_sidecar", "probe_video", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
