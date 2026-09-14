"""Canonical dashboard job-state definitions and transition rules.

This module keeps job lifecycle semantics in one place so API, workers,
tests, and future queue implementations can share the same contract.
"""
from enum import Enum
from typing import FrozenSet, Mapping


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    DONE = "done"
    ERROR = "error"
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES: FrozenSet[str] = frozenset({
    JobStatus.CANCELLED.value,
    JobStatus.DONE.value,
    JobStatus.ERROR.value,
    JobStatus.INTERRUPTED.value,
})

_ALLOWED_TRANSITIONS: Mapping[str, FrozenSet[str]] = {
    JobStatus.QUEUED.value: frozenset({
        JobStatus.RUNNING.value,
        JobStatus.CANCELLING.value,
        JobStatus.CANCELLED.value,
        JobStatus.ERROR.value,
        JobStatus.INTERRUPTED.value,
    }),
    JobStatus.RUNNING.value: frozenset({
        JobStatus.CANCELLING.value,
        JobStatus.CANCELLED.value,
        JobStatus.DONE.value,
        JobStatus.ERROR.value,
        JobStatus.INTERRUPTED.value,
    }),
    JobStatus.CANCELLING.value: frozenset({
        JobStatus.CANCELLED.value,
        JobStatus.ERROR.value,
        JobStatus.INTERRUPTED.value,
    }),
    JobStatus.CANCELLED.value: frozenset(),
    JobStatus.DONE.value: frozenset(),
    JobStatus.ERROR.value: frozenset(),
    JobStatus.INTERRUPTED.value: frozenset(),
}


def is_terminal(status: str) -> bool:
    """Return whether a job status is terminal."""
    return str(status) in TERMINAL_STATUSES


def is_valid_transition(current: str, target: str) -> bool:
    """Return whether a lifecycle transition is allowed."""
    current_value = str(current)
    target_value = str(target)
    if current_value == target_value:
        return True
    try:
        return target_value in _ALLOWED_TRANSITIONS[current_value]
    except KeyError:
        return False


def validate_transition(current: str, target: str) -> None:
    """Raise ValueError when a lifecycle transition is invalid."""
    if not is_valid_transition(current, target):
        raise ValueError(f"Invalid job status transition: {current!r} -> {target!r}")


def allowed_transitions(current: str) -> FrozenSet[str]:
    """Return the allowed next states for a status."""
    return _ALLOWED_TRANSITIONS.get(str(current), frozenset())
