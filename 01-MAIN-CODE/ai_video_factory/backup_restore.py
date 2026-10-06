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


__all__ = ["create_backup", "restore_backup", "verify_backup"]
