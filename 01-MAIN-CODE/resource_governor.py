"""Bounded resource governance helpers shared by dashboard processes."""
from __future__ import annotations

import json
import os
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
MAX_QUEUED_PER_PRINCIPAL = _int_env("AIVF_MAX_QUEUED_PER_PRINCIPAL", 5)
MAX_SSE_CONNECTIONS = _int_env("AIVF_MAX_SSE_CONNECTIONS", 16)
MAX_RENDER_WALLCLOCK_SECONDS = _int_env("AIVF_MAX_RENDER_WALLCLOCK_SECONDS", 3_600)


def principal_for_request(request: Any) -> str:
    forwarded = getattr(getattr(request, "headers", None), "get", lambda *_: None)("X-Forwarded-For")
    if forwarded:
        forwarded = str(forwarded).split(",", 1)[0].strip()
    return str(forwarded or getattr(request, "remote_addr", None) or "unknown")


def directory_size(path: str | Path, *, limit: int | None = None) -> int:
    root = Path(path)
    total = 0
    try:
        iterator = root.rglob("*")
    except OSError:
        return 0
    for item in iterator:
        try:
            if item.is_file():
                total += item.stat().st_size
                if limit is not None and total > limit:
                    return total
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
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT params FROM jobs WHERE status IN ('queued','running')"
        ).fetchall()
    count = 0
    for row in rows:
        try:
            params = json.loads(row["params"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if str(params.get("_principal", "")) == principal:
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
    "MAX_QUEUED_PER_PRINCIPAL",
    "MAX_SSE_CONNECTIONS",
    "MAX_RENDER_WALLCLOCK_SECONDS",
    "principal_for_request",
    "directory_size",
    "total_storage_bytes",
    "principal_queued_jobs",
    "check_job_creation_limits",
    "acquire_sse",
    "release_sse",
    "job_storage_ok",
]
