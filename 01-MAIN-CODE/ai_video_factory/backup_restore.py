"""SQLite and artifact backup/restore helpers for single-host deployments."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any


def _copy_sqlite(source: Path, target: Path) -> None:
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


def _copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        target.mkdir(parents=True, exist_ok=True)
        return
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def create_backup(
    *,
    state_dir: str | Path,
    knowledge_dir: str | Path,
    output_dir: str | Path,
    destination: str | Path,
    include_output: bool = True,
) -> dict[str, Any]:
    state = Path(state_dir)
    knowledge = Path(knowledge_dir)
    output = Path(output_dir)
    target_root = Path(destination)
    target_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = target_root / f"aivf-backup-{stamp}"
    suffix = 1
    while target.exists():
        target = target_root / f"aivf-backup-{stamp}-{suffix}"
        suffix += 1
    target.mkdir(parents=True, exist_ok=False)

    db = state / "jobs.db"
    if db.is_file():
        _copy_sqlite(db, target / "state" / "jobs.db")
    _copy_tree(knowledge, target / "knowledge")
    if include_output:
        _copy_tree(output, target / "output")

    manifest = {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": (target / "state" / "jobs.db").is_file(),
        "knowledge": (target / "knowledge").is_dir(),
        "output": bool(include_output),
    }
    (target / "backup_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"path": str(target), "manifest": manifest}


def restore_backup(
    *,
    backup_dir: str | Path,
    state_dir: str | Path,
    knowledge_dir: str | Path,
    output_dir: str | Path,
    restore_output: bool = True,
) -> dict[str, Any]:
    source = Path(backup_dir)
    manifest_path = source / "backup_manifest.json"
    if not manifest_path.is_file():
        raise ValueError("backup manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise ValueError("unsupported backup version")

    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    db_source = source / "state" / "jobs.db"
    if db_source.is_file():
        _copy_sqlite(db_source, state / "jobs.db")
    _copy_tree(source / "knowledge", Path(knowledge_dir))
    if restore_output and (source / "output").is_dir():
        _copy_tree(source / "output", Path(output_dir))

    return {
        "ok": True,
        "database": (state / "jobs.db").is_file(),
        "knowledge": Path(knowledge_dir).is_dir(),
        "output": Path(output_dir).is_dir() if restore_output else None,
    }


def verify_backup(backup_dir: str | Path) -> dict[str, Any]:
    root = Path(backup_dir)
    manifest_path = root / "backup_manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "error": "backup manifest is missing"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {"ok": False, "error": "backup manifest is invalid"}
    if manifest.get("version") != 1:
        return {"ok": False, "error": "unsupported backup version"}
    db_ok = not manifest.get("database") or (root / "state" / "jobs.db").is_file()
    knowledge_ok = (root / "knowledge").is_dir()
    output_ok = not manifest.get("output") or (root / "output").is_dir()
    return {
        "ok": bool(db_ok and knowledge_ok and output_ok),
        "database": db_ok,
        "knowledge": knowledge_ok,
        "output": output_ok,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os
    parser = argparse.ArgumentParser(prog="aivf-backup")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup")
    backup.add_argument("--db", required=True)
    backup.add_argument("--knowledge", required=True)
    backup.add_argument("--output", required=True)
    backup.add_argument("--backup", required=True)

    verify = sub.add_parser("verify")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--marker", default="state/backup-restore-verified")

    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--state", required=True)
    restore.add_argument("--knowledge", required=True)
    restore.add_argument("--output", required=True)

    args = parser.parse_args(list(argv or []))

    if args.command == "backup":
        destination = Path(args.backup)
        if destination.suffix == ".tar.gz":
            destination_dir = destination.parent / (destination.stem + ".snapshot")
        else:
            destination_dir = destination
        result = create_backup(
            state_dir=Path(args.db).parent,
            knowledge_dir=args.knowledge,
            output_dir=args.output,
            destination=destination_dir.parent,
            include_output=True,
        )
        archive = destination
        archive.parent.mkdir(parents=True, exist_ok=True)
        base = Path(result["path"])
        shutil.make_archive(str(archive.with_suffix("")), "gztar", root_dir=str(base.parent), base_dir=base.name)
        return 0 if archive.is_file() else 1

    if args.command == "verify":
        archive = Path(args.backup)
        if not archive.is_file():
            raise SystemExit(f"backup archive not found: {archive}")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="aivf-backup-verify-") as temp:
            shutil.unpack_archive(str(archive), temp)
            roots = [item for item in Path(temp).iterdir() if item.is_dir()]
            if not roots:
                raise SystemExit("backup archive contains no snapshot directory")
            report = verify_backup(roots[0])
            if not report["ok"]:
                raise SystemExit(json.dumps(report, sort_keys=True))
        marker = Path(args.marker)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now(timezone.utc).isoformat() + "\n", encoding="utf-8")
        return 0

    if args.command == "restore":
        archive = Path(args.backup)
        import tempfile
        with tempfile.TemporaryDirectory(prefix="aivf-backup-restore-") as temp:
            shutil.unpack_archive(str(archive), temp)
            roots = [item for item in Path(temp).iterdir() if item.is_dir()]
            if not roots:
                raise SystemExit("backup archive contains no snapshot directory")
            report = restore_backup(
                backup_dir=roots[0],
                state_dir=args.state,
                knowledge_dir=args.knowledge,
                output_dir=args.output,
            )
            if not report["ok"]:
                raise SystemExit(json.dumps(report, sort_keys=True))
        return 0

    return 1


__all__ = ["create_backup", "restore_backup", "verify_backup", "main"]
