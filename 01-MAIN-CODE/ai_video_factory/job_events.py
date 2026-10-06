"""Canonical job lifecycle event vocabulary."""
from __future__ import annotations

from enum import StrEnum


class JobEvent(StrEnum):
    JOB_CREATED = "JOB_CREATED"
    JOB_CLAIMED = "JOB_CLAIMED"
    JOB_STARTED = "JOB_STARTED"
    JOB_HEARTBEAT = "JOB_HEARTBEAT"
    JOB_CANCEL_REQUESTED = "JOB_CANCEL_REQUESTED"
    JOB_CANCELLED = "JOB_CANCELLED"
    JOB_FAILED = "JOB_FAILED"
    JOB_RETRY_SCHEDULED = "JOB_RETRY_SCHEDULED"
    JOB_COMPLETED = "JOB_COMPLETED"
    JOB_RECOVERED = "JOB_RECOVERED"
    JOB_STATUS_CHANGED = "JOB_STATUS_CHANGED"
    ARTIFACT_PUBLISHED = "ARTIFACT_PUBLISHED"


EVENT_VOCABULARY = frozenset(event.value for event in JobEvent)

__all__ = ["JobEvent", "EVENT_VOCABULARY"]
