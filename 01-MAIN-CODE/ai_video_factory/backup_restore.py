"""Verified SQLite and artifact backup/restore helpers for single-host deployments."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any


ARCHIVE_SUFFIX = ".tar.gz"
BACKUP_VERSION = 2


def _copy_sqlite(source: Path, target: Path) -> None:
    if not source.is_file():
        raise ValueError(f"database file not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source), timeout=30)
    try:
        dst = sqlite3.connect(str(target), timeout=30)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _copy_tree(source: Path, target: Path) -> bool:
    if not source.is_dir():
        return False
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return True


def _archive_base(path: Path) -> Path:
    raw = str(path)
    if not raw.endswith(ARCHIVE_SUFFIX):
        return Path(raw)
    return Path(raw[: -len(ARCHIVE_SUFFIX)])


def _find_backup_root(root: Path) -> Path:
    candidates = [p.parent for p in root.rglob("backup_manifest.json") if p.is_file()]
    if not candidates:
        raise ValueError("backup archive contains no backup_manifest.json")
    if len(candidates) != 1:
        raise ValueError("backup archive contains multiple backup manifests")
    return candidates[0]


def create_backup(
    *,
    state_dir: str | Path,
    knowledge_dir: str | Path,
    output_dir: str | Path,
    destination: str | Path,
    include_output: bool = True,
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    state = Path(state_dir)
    knowledge = Path(knowledge_dir)
    output = Path(output_dir)
    database = Path(database_path) if database_path is not None else state / "jobs.db"
    target_root = Path(destination)
    target_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = target_root / f"aivf-backup-{stamp}"
    suffix = 1
    while target.exists():
        target = target_root / f"aivf-backup-{stamp}-{suffix}"
        suffix += 1
    target.mkdir(parents=True, exist_ok=False)

    try:
        _copy_sqlite(database, target / "state" / database.name)
        knowledge_ok = _copy_tree(knowledge, target / "knowledge")
        if not knowledge_ok:
            raise ValueError("knowledge base is missing; refusing incomplete backup")
        output_ok = _copy_tree(output, target / "output") if include_output else True
        if include_output and not output_ok:
            raise ValueError("published output directory is missing; refusing incomplete backup")

        manifest = {
            "version": BACKUP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": True,
            "database_filename": database.name,
            "knowledge": True,
            "output": bool(include_output),
        }
        (target / "backup_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"path": str(target), "manifest": manifest}
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def verify_backup(backup_dir: str | Path) -> dict[str, Any]:
    root = Path(backup_dir)
    manifest_path = root / "backup_manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "error": "backup manifest is missing"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"ok": False, "error": "backup manifest is invalid"}
    if manifest.get("version") != BACKUP_VERSION:
        return {"ok": False, "error": "unsupported backup version"}

    database_filename = str(manifest.get("database_filename") or "jobs.db")
    database_ok = bool(manifest.get("database")) and (root / "state" / database_filename).is_file()
    knowledge_ok = bool(manifest.get("knowledge")) and (root / "knowledge").is_dir()
    output_ok = bool(manifest.get("output")) and (root / "output").is_dir()
    ok = bool(database_ok and knowledge_ok and output_ok)
    return {
        "ok": ok,
        "database": database_ok,
        "knowledge": knowledge_ok,
        "output": output_ok,
    }


def restore_backup(
    *,
    backup_dir: str | Path,
    state_dir: str | Path,
    knowledge_dir: str | Path,
    output_dir: str | Path,
    restore_output: bool = True,
) -> dict[str, Any]:
    source = Path(backup_dir)
    verification = verify_backup(source)
    if not verification["ok"]:
        raise ValueError(
            "backup is incomplete or corrupt: "
            + ", ".join(k for k, value in verification.items() if k != "ok" and not value)
        )

    manifest = json.loads((source / "backup_manifest.json").read_text(encoding="utf-8"))
    database_filename = str(manifest.get("database_filename") or "jobs.db")

    state = Path(state_dir)
    knowledge = Path(knowledge_dir)
    output = Path(output_dir)
    state.mkdir(parents=True, exist_ok=True)
    knowledge.parent.mkdir(parents=True, exist_ok=True)
    if restore_output:
        output.parent.mkdir(parents=True, exist_ok=True)

    database_source = source / "state" / database_filename
    if manifest.get("database"):
        _copy_sqlite(database_source, state / database_filename)
    if manifest.get("knowledge"):
        if not _copy_tree(source / "knowledge", knowledge):
            raise ValueError("backup knowledge tree disappeared during restore")
    if restore_output:
        if not manifest.get("output"):
            raise ValueError("backup does not contain the required published output tree")
        if not _copy_tree(source / "output", output):
            raise ValueError("backup output tree disappeared during restore")

    result = {
        "ok": True,
        "database": bool(manifest.get("database")) and (state / database_filename).is_file(),
        "knowledge": bool(manifest.get("knowledge")) and knowledge.is_dir(),
        "output": (
            not restore_output
            or (bool(manifest.get("output")) and output.is_dir())
        ),
    }
    if not all(result.values()):
        raise ValueError(f"restore verification failed: {result}")
    return result


def _pack_snapshot(snapshot: Path, archive: Path) -> Path:
    archive.parent.mkdir(parents=True, exist_ok=True)
    base = _archive_base(archive)
    written = Path(
        shutil.make_archive(
            str(base),
            "gztar",
            root_dir=str(snapshot.parent),
            base_dir=snapshot.name,
        )
    )
    if archive.suffixes[-2:] == [".tar", ".gz"] and written != archive:
        written.replace(archive)
        written = archive
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aivf-backup")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup")
    backup.add_argument("--db", required=True)
    backup.add_argument("--knowledge", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument("--backup", required=True)
    backup.add_argument("--staging", default=None)

    verify = sub.add_parser("verify")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--marker", default="state/backup-restore-verified")

    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--state", required=True)
    restore.add_argument("--knowledge", required=True)
    restore.add_argument("--output", required=True)

    args = parser.parse_args(argv)

    if args.command == "backup":
        archive = Path(args.backup)
        staging = Path(args.staging) if args.staging else archive.parent / ".aivf-backup-staging"
        staging.mkdir(parents=True, exist_ok=True)
        result = create_backup(
            state_dir=Path(args.db).parent,
            database_path=args.db,
            knowledge_dir=args.knowledge,
            output_dir=args.output,
            destination=staging,
            include_output=True,
        )
        snapshot = Path(result["path"])
        try:
            written = _pack_snapshot(snapshot, archive)
            verified = verify_backup(snapshot)
            if not verified["ok"] or not written.is_file():
                raise SystemExit("backup archive failed verification")
            return 0
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)
            try:
                staging.rmdir()
            except OSError:
                pass

    if args.command == "verify":
        archive = Path(args.backup)
        if not archive.is_file():
            raise SystemExit(f"backup archive not found: {archive}")
        with __import__("tempfile").TemporaryDirectory(prefix="aivf-backup-verify-") as temp:
            shutil.unpack_archive(str(archive), temp)
            root = _find_backup_root(Path(temp))
            report = verify_backup(root)
            if not report["ok"]:
                raise SystemExit(json.dumps(report, sort_keys=True))
        marker = Path(args.marker)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")
        return 0

    if args.command == "restore":
        archive = Path(args.backup)
        if not archive.is_file():
            raise SystemExit(f"backup archive not found: {archive}")
        with __import__("tempfile").TemporaryDirectory(prefix="aivf-backup-restore-") as temp:
            shutil.unpack_archive(str(archive), temp)
            root = _find_backup_root(Path(temp))
            report = restore_backup(
                backup_dir=root,
                state_dir=args.state,
                knowledge_dir=args.knowledge,
                output_dir=args.output,
            )
            if not report["ok"]:
                raise SystemExit(json.dumps(report, sort_keys=True))
        return 0

    return 1


__all__ = ["ARCHIVE_SUFFIX", "BACKUP_VERSION", "create_backup", "restore_backup", "verify_backup", "main"]
