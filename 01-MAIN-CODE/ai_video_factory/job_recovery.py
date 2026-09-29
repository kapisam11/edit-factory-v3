"""Failure classification, retry scheduling and idempotency helpers."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

TRANSIENT_MARKERS = (
    "timeout", "timed out", "temporarily unavailable", "rate limit", "429",
    "502", "503", "504", "connection reset", "connection aborted", "dns",
    "network", "busy", "resource temporarily unavailable",
)
PERMANENT_MARKERS = (
    "invalid input", "unsupported", "permission denied", "not found",
    "schema validation", "missing required", "no video stream", "corrupt",
    "malformed", "invalid credentials",
)

@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    category: str
    reason: str
    retry_after_seconds: float = 0.0

def classify_failure(error: str | BaseException) -> RetryDecision:
    text = str(error).lower()
    if any(marker in text for marker in PERMANENT_MARKERS):
        return RetryDecision(False, "permanent", "failure appears input/configuration related")
    if any(marker in text for marker in TRANSIENT_MARKERS):
        return RetryDecision(True, "transient", "failure appears recoverable")
    return RetryDecision(False, "unknown", "failure was not proven retryable")

def retry_delay(
    attempt: int,
    base_seconds: float = 2.0,
    max_seconds: float = 120.0,
    key: str = "",
) -> float:
    number = max(1, int(attempt))
    delay = min(float(max_seconds), float(base_seconds) * (2 ** min(number - 1, 8)))
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()
    jitter = (digest[0] / 255.0) * min(1.0, delay * 0.2)
    return round(min(float(max_seconds), delay + jitter), 3)

def idempotency_key(payload: Mapping[str, Any], namespace: str = "aivf") -> str:
    normalized = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"{namespace}:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:32]}"

def is_stale(
    updated_at_epoch: float,
    now: float | None = None,
    stale_after_seconds: float = 3600.0,
) -> bool:
    current = time.time() if now is None else float(now)
    return current - float(updated_at_epoch) >= float(stale_after_seconds)

def recovery_action(
    status: str,
    error: str | None = None,
    retry_count: int = 0,
    max_retries: int = 3,
) -> str:
    state = str(status or "").lower()
    if state in {"done", "success", "cancelled"}:
        return "none"
    decision = classify_failure(error or "")
    if state in {"queued", "retrying"}:
        return "wait"
    if state == "interrupted" and retry_count < max_retries:
        return "retry"
    if state == "error" and decision.retryable and retry_count < max_retries:
        return "retry"
    if state in {"running", "cancelling"}:
        return "reconcile"
    return "manual_review"

def terminal_status(status: str) -> bool:
    return str(status or "").lower() in {"done", "success", "error", "failed", "cancelled", "interrupted"}

def retryable_status(status: str, error: str | None = None) -> bool:
    return recovery_action(status, error, retry_count=0, max_retries=1) == "retry"

__all__ = [
    "RetryDecision", "classify_failure", "retry_delay", "idempotency_key",
    "is_stale", "recovery_action", "terminal_status", "retryable_status",
]
