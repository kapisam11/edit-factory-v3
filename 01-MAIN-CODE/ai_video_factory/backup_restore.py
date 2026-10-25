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
    """Safely extract regular files/directories without tar path traversal."""
    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    allowed_types = {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}
    for member in archive.getmembers():
        if member.type not in allowed_types or member.issym() or member.islnk():
            raise ValueError(f"backup contains an unsafe member: {member.name}")
        target = (root / member.name).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"backup member escapes restore root: {member.name}") from exc
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(f"backup file cannot be read: {member.name}")
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".partial",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                shutil.copyfileobj(source, handle, length=1024 * 1024)
            os.replace(temporary_path, target)
        finally:
            source.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def backup_database(source: str | Path, destination: str | Path) -> Path:
    source_path = Path(source)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_suffix(destination_path.suffix + ".partial")
    temporary.unlink(missing_ok=True)

    source_conn = sqlite3.connect(str(source_path))
    target_conn = None
    try:
        target_conn = sqlite3.connect(str(temporary))
        source_conn.backup(target_conn)
        target_conn.commit()
    finally:
        if target_conn is not None:
            target_conn.close()
        source_conn.close()

    try:
        os.replace(temporary, destination_path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    return destination_path


def _is_sensitive_state_path(relative: Path) -> bool:
    """Keep credential material out of ordinary backup archives."""
    parts = [part.casefold() for part in relative.parts]
    name = parts[-1] if parts else ""
    if any(part in {"platform-secrets", "secrets", "credentials", "backups", "backup-staging"} for part in parts):
        return True
    if name in {".env", ".env.local", "client_secret.json", "credentials.json"}:
        return True
    if "token" in name or "secret" in name or name.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return False


def _copy_tree_into_backup(
    staging_root: Path,
    archive_label: str,
    source_root: str | Path | None,
    *,
    state_tree: bool = False,
) -> list[str]:
    """Copy a tree without following symlinks; use SQLite's online backup API for DBs."""
    if source_root is None:
        return []
    source = Path(source_root)
    if not source.is_dir():
        return []
    target_root = staging_root / archive_label
    target_root.mkdir(parents=True, exist_ok=True)
    excluded: list[str] = []
    for child in sorted(source.rglob("*")):
        relative = child.relative_to(source)
        if child.is_symlink():
            continue
        if state_tree and _is_sensitive_state_path(relative):
            excluded.append((Path(archive_label) / relative).as_posix())
            continue
        destination = target_root / relative
        if child.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        if not child.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Ignore live WAL/SHM files: backup_database produces a consistent SQLite
        # snapshot, while copying those sidecars separately can reintroduce stale state.
        if child.name.casefold().endswith(("-wal", "-shm")):
            continue
        if state_tree and child.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
            backup_database(child, destination)
        else:
            shutil.copy2(child, destination)
    return excluded


def create_backup(
    *,
    db_path: str | Path,
    backup_path: str | Path,
    knowledge_root: str | Path | None = None,
    output_root: str | Path | None = None,
    uploads_root: str | Path | None = None,
    state_root: str | Path | None = None,
) -> Path:
    """Create a hash-manifested snapshot of the database and production data.

    Credential/token files under the state tree are deliberately excluded: this
    archive is a recovery backup, not a portable credential vault. Reauthorize
    provider accounts after restore. Protect the archive and store it on a
    different physical drive from the source data.
    """
    backup_path = Path(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="aivf-backup-") as temp:
        root = Path(temp)
        db_copy = backup_database(db_path, root / "jobs.db")
        excluded_sensitive: list[str] = []

        if state_root is not None:
            excluded_sensitive = _copy_tree_into_backup(
                root, "state", state_root, state_tree=True
            )
        _copy_tree_into_backup(root, "knowledge_base_v3", knowledge_root)
        _copy_tree_into_backup(root, "output", output_root)
        _copy_tree_into_backup(root, "uploads", uploads_root)

        # Hash every copied regular file, including auxiliary SQLite databases,
        # upload staging and state data. The primary jobs DB is always included.
        files: list[dict[str, object]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == "backup-manifest.json" or path.is_symlink():
                continue
            files.append({
                "name": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
        manifest = {
            "format_version": 2,
            "created_at_epoch": __import__("time").time(),
            "files": files,
            "excluded_sensitive_state_files": excluded_sensitive,
            "credential_restore_note": (
                "Token, secret, credential and platform-secrets paths are excluded. "
                "Provider accounts must be reauthorized after recovery."
            ),
        }
        (root / "backup-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
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

        database_paths = {root / "jobs.db"}
        for entry in manifest.get("files", []):
            relative = str(entry.get("name") or "")
            candidate = (root / relative).resolve()
            if candidate.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}:
                database_paths.add(candidate)
        for database_path in sorted(database_paths):
            if not database_path.is_file():
                raise ValueError(f"backup database is missing: {database_path.relative_to(root)}")
            with sqlite3.connect(str(database_path)) as conn:
                integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0] or "").lower()
            if integrity != "ok":
                raise ValueError(
                    f"backup database integrity check failed for {database_path.relative_to(root)}: {integrity}"
                )
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
                json.dumps({"backup": str(archive_path), "verified_files": verified}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return result


def restore_backup(
    backup_path: str | Path,
    destination_root: str | Path,
    *,
    replace: bool = False,
) -> Path:
    """Restore a verified backup into an isolated destination root.

    The destination is never written until the archive passes full manifest and
    SQLite integrity verification. Production deployments should restore into a
    fresh directory/volume and switch over atomically.
    """
    archive_path = Path(backup_path)
    destination = Path(destination_root).resolve()
    verify_backup(archive_path)
    if destination.exists() and any(destination.iterdir()) and not replace:
        raise ValueError(f"restore destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aivf-restore-stage-") as temp:
        stage = Path(temp)
        with tarfile.open(archive_path, "r:gz") as archive:
            _safe_extract(archive, stage)
        for child in stage.iterdir():
            target = destination / child.name
            if target.exists():
                if target.is_dir() and child.is_dir():
                    shutil.copytree(child, target, dirs_exist_ok=True, symlinks=False)
                else:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                    if child.is_dir():
                        shutil.copytree(child, target, symlinks=False)
                    else:
                        shutil.copy2(child, target)
            elif child.is_dir():
                shutil.copytree(child, target, symlinks=False)
            else:
                shutil.copy2(child, target)
    return destination


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Edit Factory backup and restore verification")
    sub = parser.add_subparsers(dest="command", required=True)

    backup = sub.add_parser("backup")
    backup.add_argument("--db", default=os.environ.get("AIVF_DB_PATH", "state/jobs.db"))
    backup.add_argument("--backup", required=True)
    backup.add_argument("--knowledge", default=os.environ.get("AIVF_KNOWLEDGE_DIR", "knowledge_base_v3"))
    backup.add_argument("--output", default=os.environ.get("AIVF_OUTPUT_DIR", "output"))
    backup.add_argument("--uploads", default=os.environ.get("AIVF_UPLOAD_DIR", "uploads"))
    backup.add_argument("--state", default=os.environ.get("AIVF_STATE_DIR", "state"))

    verify = sub.add_parser("verify")
    verify.add_argument("--backup", required=True)
    verify.add_argument("--marker", default="state/backup-restore-verified")

    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--destination", required=True)
    restore.add_argument("--replace", action="store_true")

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "backup":
        result = create_backup(
            db_path=args.db,
            backup_path=args.backup,
            knowledge_root=args.knowledge,
            output_root=args.output,
            uploads_root=args.uploads,
            state_root=args.state,
        )
        print(f"backup created: {result}")
        return 0

    if args.command == "restore":
        destination = restore_backup(
            args.backup,
            args.destination,
            replace=bool(args.replace),
        )
        print(f"backup restored to: {destination}")
        return 0

    result = verify_backup(args.backup, marker_path=args.marker)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


__all__ = ["backup_database", "create_backup", "verify_backup", "restore_backup", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
