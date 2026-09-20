"""Cross-process FFmpeg concurrency budget using short-lived SQLite leases."""
from __future__ import annotations

import contextlib
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Iterator, Sequence, Any


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


MAX_FFMPEG_CONCURRENCY = _int_env("AIVF_MAX_CONCURRENT_FFMPEG", 2)
WAIT_SECONDS = _int_env("AIVF_FFMPEG_SLOT_WAIT_SECONDS", 120)
LEASE_SECONDS = _int_env("AIVF_FFMPEG_SLOT_LEASE_SECONDS", 7_200)


def _db_path() -> str:
    configured = os.environ.get("AIVF_FFMPEG_BUDGET_DB", "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(os.environ.get("AIVF_STATE_DIR", ".")).resolve() / "ffmpeg_slots.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ffmpeg_leases("
        "token TEXT PRIMARY KEY, pid INTEGER NOT NULL, acquired REAL NOT NULL)"
    )
    return conn


@contextlib.contextmanager
def slot() -> Iterator[None]:
    deadline = time.monotonic() + WAIT_SECONDS
    token = uuid.uuid4().hex
    acquired = False
    while time.monotonic() < deadline:
        now = time.time()
        try:
            with _connect() as conn:
                conn.execute(
                    "DELETE FROM ffmpeg_leases WHERE acquired < ?",
                    (now - LEASE_SECONDS,),
                )
                conn.execute("BEGIN IMMEDIATE")
                count = conn.execute("SELECT COUNT(*) FROM ffmpeg_leases").fetchone()[0]
                if count < MAX_FFMPEG_CONCURRENCY:
                    conn.execute(
                        "INSERT INTO ffmpeg_leases(token,pid,acquired) VALUES(?,?,?)",
                        (token, os.getpid(), now),
                    )
                    conn.commit()
                    acquired = True
                    break
                conn.rollback()
        except sqlite3.OperationalError:
            pass
        time.sleep(0.1)
    if not acquired:
        raise RuntimeError(
            f"FFmpeg concurrency budget exhausted ({MAX_FFMPEG_CONCURRENCY} slots)"
        )
    try:
        yield
    finally:
        try:
            with _connect() as conn:
                conn.execute("DELETE FROM ffmpeg_leases WHERE token=?", (token,))
        except sqlite3.Error:
            pass


def run_ffmpeg_subprocess(command: Sequence[str], **kwargs: Any):
    with slot():
        return __import__("subprocess").run(list(command), **kwargs)


def wrap_run_ffmpeg(original):
    def guarded(*args, **kwargs):
        with slot():
            return original(*args, **kwargs)
    guarded.__name__ = getattr(original, "__name__", "run_ffmpeg")
    guarded.__doc__ = getattr(original, "__doc__", None)
    return guarded


__all__=["MAX_FFMPEG_CONCURRENCY","WAIT_SECONDS","LEASE_SECONDS","slot","run_ffmpeg_subprocess","wrap_run_ffmpeg"]
