"""Shared retry classification, bounded backoff, staleness and idempotency rules."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any, Mapping

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

def is_retryable_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    if isinstance(error, sqlite3.OperationalError):
        return any(marker in str(error).lower() for marker in ("locked", "busy"))
    if isinstance(error, OSError) and not isinstance(error, FileNotFoundError):
        return True
    text = str(error).lower()
    if any(marker in text for marker in NON_RETRYABLE_TEXT):
        return False
    if any(marker in text for marker in ("429", "temporarily unavailable", "timeout", "timed out", "rate limit", "connection reset", "502", "503", "504")):
        return True
    return isinstance(error, RuntimeError)

def classify_failure(error: Exception) -> str:
    text = str(error).lower()
    if any(marker in text for marker in NON_RETRYABLE_TEXT):
        return "permanent"
    if is_retryable_error(error):
        return "transient"
    return "unknown"

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

def is_stale(updated_at_epoch: float, *, now: float | None = None, stale_after_seconds: float = 3600.0) -> bool:
    current = time.time() if now is None else float(now)
    return current - float(updated_at_epoch) >= float(stale_after_seconds)

__all__ = ["is_retryable_error","classify_failure","backoff_seconds","retry_after","idempotency_key","is_stale"]
