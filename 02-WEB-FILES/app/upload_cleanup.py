"""Safe cleanup for abandoned dashboard uploads.

Uploads are retained while referenced by a persisted job. Unreferenced files are
removed only after a conservative retention period so a temporary restart or
slow job cannot race the cleanup.
"""
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional, Set, Tuple

logger = logging.getLogger(__name__)

RETENTION_SECONDS = int(os.environ.get("AIVF_UPLOAD_RETENTION_SECONDS", str(24 * 60 * 60)))
TEMP_RETENTION_SECONDS = int(os.environ.get("AIVF_UPLOAD_TEMP_RETENTION_SECONDS", str(60 * 60)))


def _configured_paths() -> Tuple[Path, Path]:
    app_dir = Path(__file__).resolve().parent
    source_web_dir = app_dir.parent if (app_dir.parent / "templates").is_dir() else None
    installed_web_dir = Path(os.environ.get("AIVF_WEB_BASE_DIR", Path(sys.prefix) / "share" / "ai-video-factory"))
    base_dir = source_web_dir or installed_web_dir
    upload_dir = Path(os.environ.get("AIVF_UPLOAD_DIR", base_dir / "uploads")).resolve()
    state_dir = Path(os.environ.get("AIVF_STATE_DIR", base_dir / "state")).resolve()
    return upload_dir, state_dir / "jobs.db"


def _referenced_uploads(db_path: Path, upload_dir: Path) -> Set[Path]:
    if not db_path.exists():
        return set()
    referenced: Set[Path] = set()
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            rows = conn.execute("SELECT params FROM jobs").fetchall()
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Upload cleanup skipped: cannot read job database: %s", exc)
        return set()

    root = upload_dir.resolve()
    for (raw_params,) in rows:
        try:
            params = json.loads(raw_params or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        raw_video = params.get("raw_video")
        if not raw_video:
            continue
        try:
            candidate = Path(str(raw_video)).resolve()
        except OSError:
            continue
        if candidate == root or root not in candidate.parents:
            continue
        referenced.add(candidate)
    return referenced


def cleanup_orphan_uploads(now: Optional[float] = None) -> int:
    """Delete stale upload files that are no longer referenced by any job."""
    upload_dir, db_path = _configured_paths()
    if not upload_dir.is_dir():
        return 0
    now = time.time() if now is None else now
    referenced = _referenced_uploads(db_path, upload_dir)
    removed = 0

    try:
        entries = list(upload_dir.iterdir())
    except OSError as exc:
        logger.warning("Upload cleanup skipped: cannot list upload directory: %s", exc)
        return 0

    for path in entries:
        if not path.is_file():
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        threshold = TEMP_RETENTION_SECONDS if path.name.startswith(".upload-") else RETENTION_SECONDS
        if age < threshold:
            continue
        if path.resolve() in referenced:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.warning("Could not remove orphan upload %s: %s", path, exc)
    if removed:
        logger.info("Removed %d orphaned dashboard upload(s)", removed)
    return removed


try:
    cleanup_orphan_uploads()
except Exception:
    logger.exception("Unexpected error during upload cleanup")
