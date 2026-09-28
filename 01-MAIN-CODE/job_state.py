"""Backward-compatible re-export of the canonical package job-state contract."""
from ai_video_factory.job_state import (
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
    JobStatus,
    allowed_transitions,
    is_terminal,
    is_valid_transition,
    validate_transition,
)

__all__ = [
    "JobStatus",
    "TERMINAL_STATUSES",
    "RETRYABLE_STATUSES",
    "is_terminal",
    "is_valid_transition",
    "validate_transition",
    "allowed_transitions",
]
