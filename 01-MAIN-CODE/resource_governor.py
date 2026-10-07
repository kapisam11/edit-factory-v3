"""Bounded resource governance helpers shared by dashboard processes."""
from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Iterable


class ResourceLimitExceeded(RuntimeError):
    pass


_ACTIVE_SSE = 0
_ACTIVE_SSE_LOCK = threading.Lock()


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


MAX_TOTAL_STORAGE_BYTES = _int_env("AIVF_MAX_TOTAL_STORAGE_MB", 20_000) * 1024 * 1024
MAX_JOB_STORAGE_BYTES = _int_env("AIVF_MAX_JOB_DISK_MB", 4_096) * 1024 * 1024
MAX_RESERVED_DISK_BYTES = _int_env("AIVF_MAX_RESERVED_DISK_MB", 20_000) * 1024 * 1024
MAX_RESERVED_MEMORY_BYTES = _int_env("AIVF_MAX_RESERVED_MEMORY_MB", 16_384) * 1024 * 1024
try:
    MAX_CPU_WEIGHT = float(os.environ.get("AIVF_MAX_CPU_WEIGHT", "8.0"))
except ValueError as exc:
    raise ValueError("AIVF_MAX_CPU_WEIGHT must be numeric") from exc
if MAX_CPU_WEIGHT <= 0:
    raise ValueError("AIVF_MAX_CPU_WEIGHT must be > 0")
MAX_QUEUED_PER_PRINCIPAL = _int_env("AIVF_MAX_QUEUED_PER_PRINCIPAL", 5)
MAX_SSE_CONNECTIONS = min(3, _int_env("AIVF_MAX_SSE_CONNECTIONS", 2))
MAX_SSE_LIFETIME_SECONDS = _int_env("AIVF_SSE_MAX_SECONDS", 900)
MAX_RENDER_WALLCLOCK_SECONDS = _int_env("AIVF_MAX_RENDER_WALLCLOCK_SECONDS", 3_600)
RESOURCE_CHECK_INTERVAL_SECONDS = max(1.0, float(os.environ.get("AIVF_RESOURCE_CHECK_INTERVAL_SECONDS", "5")))
RESOURCE_RECONCILE_INTERVAL_SECONDS = max(
    RESOURCE_CHECK_INTERVAL_SECONDS,
    float(os.environ.get("AIVF_RESOURCE_RECONCILE_INTERVAL_SECONDS", "30")),
)


def principal_for_request(request: Any) -> str:
    """Return a stable authenticated-session principal, with a safe network fallback."""
    try:
        from flask import has_request_context, session

        if has_request_context() and session.get("aivf_authenticated"):
            principal = str(session.get("aivf_principal") or "").strip()
            if not principal:
                principal = str(session.get("aivf_user_id") or "").strip()
            if not principal:
                principal = "session:" + secrets.token_urlsafe(24)
                session["aivf_principal"] = principal
            return principal
    except (ImportError, RuntimeError, TypeError):
        pass

    remote = str(getattr(request, "remote_addr", None) or "unknown")
    if os.environ.get("AIVF_TRUST_PROXY_HEADERS", "0") == "1":
        forwarded = getattr(getattr(request, "headers", None), "get", lambda *_: None)("X-Forwarded-For")
        if forwarded:
            return str(forwarded).split(",", 1)[0].strip() or remote
    return remote


def directory_size(path: str | Path, *, limit: int | None = None) -> int:
    """Count only regular files beneath a root; never follow symlinked files/directories."""
    root = Path(path)
    total = 0
    if not root.exists() or root.is_symlink():
        return 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        total += int(entry.stat(follow_symlinks=False).st_size)
                        if limit is not None and total > limit:
                            return total
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def total_storage_bytes(*roots: str | Path, limit: int | None = None) -> int:
    total = 0
    for root in roots:
        total += directory_size(root, limit=None if limit is None else max(0, limit - total))
        if limit is not None and total > limit:
            return total
    return total


def principal_queued_jobs(db_connect, principal: str) -> int:
    """Count queued/running jobs for a principal across current and legacy schemas."""
    with db_connect() as conn:
        columns = {
            str(row["name"] if hasattr(row, "keys") else row[1])
            for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "principal" in columns:
            rows = conn.execute(
                "SELECT principal, params FROM jobs WHERE status IN ('queued','running')"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT params FROM jobs WHERE status IN ('queued','running')"
            ).fetchall()

    count = 0
    for row in rows:
        try:
            if hasattr(row, "keys"):
                raw_params = row["params"]
                row_principal = str(row["principal"] if "principal" in row.keys() else "").strip()
            else:
                raw_params = row[1] if "principal" in columns else row[0]
                row_principal = str(row[0] if "principal" in columns else "").strip()
            params = json.loads(raw_params or "{}")
        except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if row_principal == principal or str(params.get("_principal", "")).strip() == principal:
            count += 1
    return count


def check_job_creation_limits(db_connect, principal: str, upload_roots: Iterable[str | Path]) -> None:
    if principal_queued_jobs(db_connect, principal) >= MAX_QUEUED_PER_PRINCIPAL:
        raise ResourceLimitExceeded("principal queue capacity reached")
    if total_storage_bytes(*upload_roots, limit=MAX_TOTAL_STORAGE_BYTES) >= MAX_TOTAL_STORAGE_BYTES:
        raise ResourceLimitExceeded("total storage quota reached")


def acquire_sse() -> bool:
    global _ACTIVE_SSE
    with _ACTIVE_SSE_LOCK:
        if _ACTIVE_SSE >= MAX_SSE_CONNECTIONS:
            return False
        _ACTIVE_SSE += 1
        return True


def release_sse() -> None:
    global _ACTIVE_SSE
    with _ACTIVE_SSE_LOCK:
        _ACTIVE_SSE = max(0, _ACTIVE_SSE - 1)


def job_storage_ok(package_dir: str | Path) -> bool:
    return directory_size(package_dir, limit=MAX_JOB_STORAGE_BYTES) <= MAX_JOB_STORAGE_BYTES


__all__ = [
    "ResourceLimitExceeded",
    "MAX_TOTAL_STORAGE_BYTES",
    "MAX_JOB_STORAGE_BYTES",
    "MAX_RESERVED_DISK_BYTES",
    "MAX_RESERVED_MEMORY_BYTES",
    "MAX_CPU_WEIGHT",
    "MAX_QUEUED_PER_PRINCIPAL",
    "MAX_SSE_CONNECTIONS",
    "MAX_SSE_LIFETIME_SECONDS",
    "MAX_RENDER_WALLCLOCK_SECONDS",
    "RESOURCE_CHECK_INTERVAL_SECONDS",
    "RESOURCE_RECONCILE_INTERVAL_SECONDS",
    "principal_for_request",
    "directory_size",
    "total_storage_bytes",
    "principal_queued_jobs",
    "check_job_creation_limits",
    "acquire_sse",
    "release_sse",
    "job_storage_ok",
]
