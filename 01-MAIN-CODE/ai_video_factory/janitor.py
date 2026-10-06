"""Bounded operational cleanup for the single-host SQLite deployment."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any


def cleanup_operational_state(
    *,
    db_path: str | Path,
    upload_dir: str | Path | None = None,
    output_dir: str | Path | None = None,
    job_retention_days: int = 30,
    log_retention_days: int = 7,
    event_retention_days: int = 30,
    idempotency_retention_seconds: int = 86400,
    workspace_retention_seconds: int = 86400,
) -> dict[str, int]:
    db = Path(db_path)
    stats = {
        "rate_limits": 0,
        "idempotency_keys": 0,
        "job_logs": 0,
        "job_events": 0,
        "workspaces": 0,
        "old_jobs": 0,
        "vacuumed": 0,
    }
    if not db.is_file():
        return stats

    with sqlite3.connect(db, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        stats["rate_limits"] = int(conn.execute(
            "DELETE FROM rate_limits WHERE ts < ?",
            (time.time() - max(60, int(idempotency_retention_seconds)),),
        ).rowcount)
        stats["idempotency_keys"] = int(conn.execute(
            "DELETE FROM idempotency_keys WHERE created_at < unixepoch() - ?",
            (max(60, int(idempotency_retention_seconds)),),
        ).rowcount)
        stats["job_logs"] = int(conn.execute(
            "DELETE FROM job_logs WHERE created_at < datetime('now', ?)",
            (f"-{max(1, int(log_retention_days))} days",),
        ).rowcount)
        stats["job_events"] = int(conn.execute(
            "DELETE FROM job_events WHERE created_at < datetime('now', ?)",
            (f"-{max(1, int(event_retention_days))} days",),
        ).rowcount)
        cutoff = f"-{max(1, int(job_retention_days))} days"
        stats["old_jobs"] = int(conn.execute(
            "DELETE FROM jobs WHERE created_at < datetime('now', ?) "
            "AND status IN ('done','cancelled') "
            "AND NOT EXISTS (SELECT 1 FROM job_artifacts WHERE job_artifacts.job_id = jobs.id)",
            (cutoff,),
        ).rowcount)
        conn.execute("ANALYZE")
        if str(__import__("os").environ.get("AIVF_JANITOR_VACUUM", "")).strip().lower() in {"1", "true", "yes"}:
            conn.execute("VACUUM")
            stats["vacuumed"] = 1
        conn.commit()

        active_rows = conn.execute(
            "SELECT workspace_dir FROM jobs WHERE workspace_dir IS NOT NULL "
            "AND status IN ('queued','running','cancelling')"
        ).fetchall()
    active_workspaces = {
        str(Path(str(row[0])).resolve())
        for row in active_rows
        if row and row[0]
    }

    if output_dir:
        root = Path(output_dir).resolve() / ".workspaces"
        if root.is_dir():
            now = time.time()
            for path in root.glob("*/*"):
                if not path.is_dir():
                    continue
                try:
                    if str(path.resolve()) in active_workspaces:
                        continue
                    if now - path.stat().st_mtime < max(60, int(workspace_retention_seconds)):
                        continue
                    shutil.rmtree(path, ignore_errors=True)
                    stats["workspaces"] += 1
                except OSError:
                    continue

    return stats


def main(argv: list[str] | None = None) -> int:
    import os
    args = list(argv or [])
    db_path = args[0] if args else os.environ.get("AIVF_STATE_DIR", "state")
    base = Path(db_path)
    if base.is_dir() or base.suffix == "":
        base = base / "jobs.db"
    output_dir = os.environ.get("AIVF_OUTPUT_DIR", "output")
    report = cleanup_operational_state(
        db_path=base,
        upload_dir=os.environ.get("AIVF_UPLOAD_DIR", "uploads"),
        output_dir=output_dir,
    )
    print(json.dumps(report, sort_keys=True))
    return 0


__all__ = ["cleanup_operational_state", "main"]
