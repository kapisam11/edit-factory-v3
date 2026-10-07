"""Shared retry classification, bounded backoff, staleness and idempotency rules."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Mapping

from .structured_errors import ErrorCode, JobError, classify_exception

NON_RETRYABLE_TEXT = (
    "invalid blueprint",
    "schema validation",
    "unsupported file",
    "invalid configuration",
    "unknown edit type",
    "missing or not a file",
    "no video stream",
    "invalid credentials",
)

def classify_job_error(error: Exception) -> JobError:
    if isinstance(error, TimeoutError):
        return JobError(ErrorCode.TOOL_TIMEOUT, str(error) or "operation timed out", True)
    if isinstance(error, ConnectionError):
        return JobError(ErrorCode.TOOL_FAILED, str(error) or "connection failure", True)
    if isinstance(error, sqlite3.OperationalError):
        message = str(error)
        if "locked" in message.lower() or "busy" in message.lower():
            return JobError(ErrorCode.DATABASE_BUSY, message, True)
    if isinstance(error, PermissionError):
        return JobError(ErrorCode.INTERNAL, str(error), False)
    if isinstance(error, FileNotFoundError):
        return JobError(ErrorCode.TOOL_MISSING, str(error), False)
    if isinstance(error, OSError):
        return JobError(ErrorCode.TOOL_FAILED, str(error), True)
    return classify_exception(error)


def is_retryable_error(error: Exception) -> bool:
    return bool(classify_job_error(error).retryable)


def classify_failure(error: Exception) -> str:
    job_error = classify_job_error(error)
    if job_error.retryable:
        return "transient"
    if job_error.code == ErrorCode.INTERNAL:
        return "unknown"
    return "permanent"

def backoff_seconds(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    return min(cap, base * (2 ** (attempt - 1)))

def retry_after(attempt: int, *, base: float = 0.5, cap: float = 8.0, key: str = "") -> float:
    delay = backoff_seconds(attempt, base=base, cap=cap)
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()
    return min(cap, round(delay + (digest[0] / 255.0) * min(delay * 0.2, 1.0), 3))

def idempotency_key(payload: Mapping[str, Any], *, namespace: str = "aivf") -> str:
    normalized = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"{namespace}:{digest}"

def retry_deadline_exceeded(started_at_epoch: float, *, now: float | None = None, max_seconds: float = 3600.0) -> bool:
    current = time.time() if now is None else float(now)
    return current - float(started_at_epoch) >= float(max_seconds)


def is_stale(updated_at_epoch: float, *, now: float | None = None, stale_after_seconds: float = 3600.0) -> bool:
    current = time.time() if now is None else float(now)
    return current - float(updated_at_epoch) >= float(stale_after_seconds)

__all__ = ["is_retryable_error","classify_failure","backoff_seconds","retry_after","idempotency_key","retry_deadline_exceeded","is_stale"]
