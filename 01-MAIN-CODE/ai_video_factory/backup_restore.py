"""SQLite/media backup and restore verification for Edit Factory deployments."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from typing import Iterable


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        target = (root / member.name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"backup member escapes restore root: {member.name}") from exc
        if member.issym() or member.islnk():
            raise ValueError(f"backup contains an unsafe link: {member.name}")
    archive.extractall(destination)


def backup_database(source: str | Path, destination: str | Path) -> Path:
    source_path = Path(source)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_suffix(destination_path.suffix + ".partial")
    temporary.unlink(missing_ok=True)

    with sqlite3.connect(str(source_path)) as source_conn, sqlite3.connect(str(temporary)) as target_conn:
        source_conn.backup(target_conn)
    os.replace(temporary, destination_path)
    return destination_path


def create_backup(
    *,
    db_path: str | Path,
    backup_path: str | Path,
    knowledge_root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> Path:
    backup_path = Path(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="aivf-backup-") as temp:
        root = Path(temp)
        db_copy = backup_database(db_path, root / "jobs.db")
        files: list[dict[str, object]] = [
            {
                "name": "jobs.db",
                "size_bytes": db_copy.stat().st_size,
                "sha256": _sha256(db_copy),
            }
        ]

        for label, raw_root in (("knowledge_base_v3", knowledge_root), ("output", output_root)):
            if raw_root is None:
                continue
            source_root = Path(raw_root)
            if not source_root.is_dir():
                continue
            target_root = root / label
            shutil.copytree(source_root, target_root, dirs_exist_ok=True, symlinks=False)
            for path in target_root.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    files.append({
                        "name": path.relative_to(root).as_posix(),
                        "size_bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    })

        manifest = {
            "format_version": 1,
            "created_at_epoch": __import__("time").time(),
            "files": files,
        }
        (root / "backup-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "
",
            encoding="utf-8",
        )

        partial = backup_path.with_suffix(backup_path.suffix + ".partial")
        partial.unlink(missing_ok=True)
        with tarfile.open(partial, "w:gz") as archive:
            for child in root.iterdir():
                archive.add(child, arcname=child.name, recursive=True)
        os.replace(partial, backup_path)

    return backup_path


def verify_backup(
    backup_path: str | Path,
    *,
    marker_path: str | Path | None = None,
) -> dict[str, object]:
    archive_path = Path(backup_path)
    if not archive_path.is_file():
        raise ValueError(f"backup archive does not exist: {archive_path}")

    with tempfile.TemporaryDirectory(prefix="aivf-restore-check-") as temp:
        root = Path(temp)
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_extract(archive, root)

        manifest_path = root / "backup-manifest.json"
        if not manifest_path.is_file():
            raise ValueError("backup manifest is missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        verified = 0
        for entry in manifest.get("files", []):
            relative = str(entry.get("name") or "")
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError as exc:
                raise ValueError(f"manifest path escapes backup: {relative}") from exc
            if not candidate.is_file():
                raise ValueError(f"backup file is missing: {relative}")
            expected = str(entry.get("sha256") or "").lower()
            actual = _sha256(candidate).lower()
            if not expected or actual != expected:
                raise ValueError(f"backup hash mismatch: {relative}")
            verified += 1

        jobs = root / "jobs.db"
        sqlite3.connect(str(jobs)).execute("PRAGMA integrity_check").fetchone()
        result = {
            "ok": True,
            "archive": str(archive_path),
            "verified_files": verified,
            "manifest_format_version": manifest.get("format_version"),
        }
        if marker_path is not None:
            marker = Path(marker_path)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                json.dumps({"backup": str(archive_path), "verified_files": verified}, sort_keys=True) + "
",
                encoding="utf-8",
            )
        return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edit Factory backup and restore verification")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup")
    backup.add_argument("--db", default=os.environ.get("AIVF_DB_PATH", "state/jobs.db"))
    backup.add_argument("--backup", required=True)
    backup.add_argument("--knowledge", default=os.environ.get("AIVF_KNOWLEDGE_DIR", "knowledge_base_v3"))
    backup.add_argument("--output", default=os.environ.get("AIVF_OUTPUT_DIR", "output"))

    verify = sub.add_parser("verify")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--marker", default="state/backup-restore-verified")

    args = parser.parse_args(list(argv or []))
    if args.command == "backup":
        result = create_backup(
            db_path=args.db,
            backup_path=args.backup,
            knowledge_root=args.knowledge,
            output_root=args.output,
        )
        print(f"backup created: {result}")
        return 0

    result = verify_backup(args.backup, marker_path=args.marker)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


__all__ = ["backup_database", "create_backup", "verify_backup", "main"]
