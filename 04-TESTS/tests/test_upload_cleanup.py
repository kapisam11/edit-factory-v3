import json
import os
import sqlite3
import time
from pathlib import Path

from app import upload_cleanup


def _make_db(path: Path, params_list):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE jobs (params TEXT NOT NULL)")
        for params in params_list:
            conn.execute("INSERT INTO jobs(params) VALUES (?)", (json.dumps(params),))


def test_cleanup_removes_stale_unreferenced_upload(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    state_dir = tmp_path / "state"
    upload_dir.mkdir()
    state_dir.mkdir()
    db = state_dir / "jobs.db"
    stale = upload_dir / "stale.mp4"
    stale.write_bytes(b"x")
    old = time.time() - 2 * 24 * 60 * 60
    os.utime(stale, (old, old))

    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("AIVF_STATE_DIR", str(state_dir))
    _make_db(db, [])

    assert upload_cleanup.cleanup_orphan_uploads(now=time.time()) == 1
    assert not stale.exists()


def test_cleanup_preserves_referenced_upload(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    state_dir = tmp_path / "state"
    upload_dir.mkdir()
    state_dir.mkdir()
    db = state_dir / "jobs.db"
    referenced = upload_dir / "kept.mp4"
    referenced.write_bytes(b"x")
    old = time.time() - 2 * 24 * 60 * 60
    os.utime(referenced, (old, old))

    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("AIVF_STATE_DIR", str(state_dir))
    _make_db(db, [{"raw_video": str(referenced)}])

    assert upload_cleanup.cleanup_orphan_uploads(now=time.time()) == 0
    assert referenced.exists()


def test_cleanup_removes_stale_temp_upload_quickly(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    state_dir = tmp_path / "state"
    upload_dir.mkdir()
    state_dir.mkdir()
    db = state_dir / "jobs.db"
    temp = upload_dir / ".upload-abandoned.mp4"
    temp.write_bytes(b"x")
    old = time.time() - 2 * 60 * 60
    os.utime(temp, (old, old))

    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("AIVF_STATE_DIR", str(state_dir))
    _make_db(db, [])

    assert upload_cleanup.cleanup_orphan_uploads(now=time.time()) == 1
    assert not temp.exists()
