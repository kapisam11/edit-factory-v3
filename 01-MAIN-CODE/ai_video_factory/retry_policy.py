"""Shared retry classification and bounded exponential backoff rules."""
from __future__ import annotations

import sqlite3


NON_RETRYABLE_TEXT = (
    "invalid blueprint",
    "schema validation",
    "unsupported file",
    "invalid configuration",
    "unknown edit type",
    "missing or not a file",
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
    if any(marker in text for marker in ("429", "temporarily unavailable", "timeout", "rate limit", "connection reset")):
        return True
    # Unknown runtime failures remain bounded by the caller retry budget.
    return isinstance(error, RuntimeError)


def backoff_seconds(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    return min(cap, base * (2 ** (attempt - 1)))


__all__ = ["is_retryable_error", "backoff_seconds"]
