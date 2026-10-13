from __future__ import annotations

import sqlite3
import shutil
import tarfile
from pathlib import Path

import pytest

from ai_video_factory.backup_restore import create_backup, restore_backup, verify_backup


def _make_db(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS marker (value TEXT NOT NULL)")
        connection.execute("DELETE FROM marker")
        connection.execute("INSERT INTO marker(value) VALUES (?)", (marker,))


def test_backup_includes_uploads_and_all_non_secret_state_databases(tmp_path):
    state = tmp_path / "state"
    main_db = state / "jobs.db"
    _make_db(main_db, "dashboard-job-state")
    publisher_db = state / "folder-publisher.sqlite"
    _make_db(publisher_db, "per-platform-publication-state")
    (state / "publishing-heartbeat.json").write_text('{"status":"ok"}', encoding="utf-8")
    (state / "youtube-token.json").write_text('{"access_token":"do-not-back-up"}', encoding="utf-8")
    _make_db(state / "platform-secrets" / "platform-tokens.sqlite", "secret-token-db")

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "upload-sidecar.json").write_text('{"approval":true}', encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    (output / "final.v3.mp4").write_bytes(b"final artifact")
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / "index.json").write_text('{"knowledge":true}', encoding="utf-8")

    archive = tmp_path / "backup-drive" / "edit-factory.tar.gz"
    create_backup(
        db_path=main_db,
        backup_path=archive,
        knowledge_root=knowledge,
        output_root=output,
        uploads_root=uploads,
        state_root=state,
    )

    verification = verify_backup(archive)
    assert verification["ok"] is True
    restored = tmp_path / "restored"
    restore_backup(archive, restored)

    assert (restored / "jobs.db").is_file()
    assert (restored / "state" / "folder-publisher.sqlite").is_file()
    assert (restored / "state" / "publishing-heartbeat.json").is_file()
    assert (restored / "uploads" / "upload-sidecar.json").is_file()
    assert (restored / "output" / "final.v3.mp4").read_bytes() == b"final artifact"
    assert (restored / "knowledge_base_v3" / "index.json").is_file()
    assert not (restored / "state" / "youtube-token.json").exists()
    assert not (restored / "state" / "platform-secrets").exists()

    with sqlite3.connect(restored / "state" / "folder-publisher.sqlite") as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "per-platform-publication-state"

    with sqlite3.connect(restored / "jobs.db") as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "dashboard-job-state"


def _make_simple_backup(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    main_db = state / "jobs.db"
    _make_db(main_db, "restart-recovery")
    archive = tmp_path / "backup.tar.gz"
    create_backup(db_path=main_db, backup_path=archive, state_root=state)
    return archive


def test_restore_rejects_corrupt_archive_before_touching_destination(tmp_path: Path) -> None:
    archive = tmp_path / "corrupt.tar.gz"
    archive.write_bytes(b"not a valid backup archive")
    destination = tmp_path / "restored"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("preserve existing data", encoding="utf-8")

    with pytest.raises((tarfile.TarError, OSError, EOFError, ValueError)):
        restore_backup(archive, destination, replace=True)

    assert sentinel.read_text(encoding="utf-8") == "preserve existing data"


def test_restore_rejects_tampered_archive_by_manifest_hash_before_writes(tmp_path: Path) -> None:
    archive = _make_simple_backup(tmp_path)
    extracted = tmp_path / "repacked"
    extracted.mkdir()
    with tarfile.open(archive, "r:gz") as original:
        for member in original.getmembers():
            target = (extracted / member.name).resolve()
            target.relative_to(extracted.resolve())
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = original.extractfile(member)
            if source is None:
                continue
            try:
                with target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
            finally:
                source.close()

    jobs_db = extracted / "jobs.db"
    jobs_db.write_bytes(jobs_db.read_bytes() + b"tampered")
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as archive_writer:
        for child in extracted.iterdir():
            archive_writer.add(child, arcname=child.name, recursive=True)

    destination = tmp_path / "restore-target"
    with pytest.raises(ValueError, match="hash mismatch"):
        restore_backup(tampered, destination)

    assert not destination.exists()


def test_restore_does_not_overwrite_nonempty_destination_without_explicit_replace(
    tmp_path: Path,
) -> None:
    archive = _make_simple_backup(tmp_path)
    destination = tmp_path / "restore-target"
    destination.mkdir()
    sentinel = destination / "operator-data.txt"
    sentinel.write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(ValueError, match="destination is not empty"):
        restore_backup(archive, destination)

    assert sentinel.read_text(encoding="utf-8") == "do not overwrite"
