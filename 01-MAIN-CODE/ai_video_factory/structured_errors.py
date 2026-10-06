"""Machine-readable error categories for job lifecycle and retry decisions."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    INPUT_INVALID = "input_invalid"
    INPUT_TOO_LARGE = "input_too_large"
    MEDIA_UNSUPPORTED = "media_unsupported"
    DISK_FULL = "disk_full"
    RESOURCE_LIMIT = "resource_limit"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_MISSING = "tool_missing"
    TOOL_FAILED = "tool_failed"
    RIGHTS_BLOCKED = "rights_blocked"
    BLUEPRINT_INVALID = "blueprint_invalid"
    VALIDATION_FAILED = "validation_failed"
    RENDER_FAILED = "render_failed"
    DATABASE_BUSY = "database_busy"
    WORKER_CRASH = "worker_crash"
    INTERNAL = "internal"


@dataclass(frozen=True)
class JobError:
    code: ErrorCode
    message: str
    retryable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }


def classify_exception(exc: BaseException) -> JobError:
    message = str(exc).strip() or type(exc).__name__
    lowered = message.lower()
    if "timed out" in lowered or "timeout" in lowered:
        return JobError(ErrorCode.TOOL_TIMEOUT, message, True)
    if "database is locked" in lowered or "database is busy" in lowered:
        return JobError(ErrorCode.DATABASE_BUSY, message, True)
    if "disk" in lowered and ("space" in lowered or "full" in lowered):
        return JobError(ErrorCode.DISK_FULL, message, False)
    if "resource limit" in lowered or "capacity" in lowered or "reserved" in lowered:
        return JobError(ErrorCode.RESOURCE_LIMIT, message, False)
    if "rights" in lowered or "publish blocked" in lowered:
        return JobError(ErrorCode.RIGHTS_BLOCKED, message, False)
    if "blueprint" in lowered and ("invalid" in lowered or "failed" in lowered):
        return JobError(ErrorCode.BLUEPRINT_INVALID, message, False)
    if "unsupported" in lowered and ("codec" in lowered or "media" in lowered):
        return JobError(ErrorCode.MEDIA_UNSUPPORTED, message, False)
    if "upload" in lowered and ("large" in lowered or "size" in lowered):
        return JobError(ErrorCode.INPUT_TOO_LARGE, message, False)
    if "ffmpeg" in lowered or "ffprobe" in lowered:
        return JobError(ErrorCode.TOOL_FAILED, message, False)
    if "validation" in lowered or "failed strict editorial qc" in lowered:
        return JobError(ErrorCode.VALIDATION_FAILED, message, False)
    return JobError(ErrorCode.INTERNAL, message, False)


__all__ = ["ErrorCode", "JobError", "classify_exception"]
