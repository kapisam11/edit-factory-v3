"""Machine-readable failure taxonomy with backward-compatible human messages."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    INPUT_INVALID = "input_invalid"
    INPUT_TOO_LARGE = "input_too_large"
    MEDIA_UNSUPPORTED = "media_unsupported"
    DISK_FULL = "disk_full"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_MISSING = "tool_missing"
    RENDER_FAILED = "render_failed"
    VALIDATION_FAILED = "validation_failed"
    RIGHTS_FAILED = "rights_failed"
    CONFIG_INVALID = "config_invalid"
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


def classify_exception(exc: Exception) -> JobError:
    from .retry_policy import is_retryable_error

    message = str(exc)
    text = message.lower()
    name = type(exc).__name__.lower()

    if "too large" in text or "exceeds the hard size" in text:
        code = ErrorCode.INPUT_TOO_LARGE
    elif "unsupported" in text or "signature check" in text or "no video stream" in text:
        code = ErrorCode.MEDIA_UNSUPPORTED
    elif "disk" in text and ("space" in text or "full" in text):
        code = ErrorCode.DISK_FULL
    elif "timeout" in text or "timed out" in text or "tool timeout" in text:
        code = ErrorCode.TOOL_TIMEOUT
    elif "not found" in text and ("ffmpeg" in text or "ffprobe" in text):
        code = ErrorCode.TOOL_MISSING
    elif "rights" in text or "compliance" in text:
        code = ErrorCode.RIGHTS_FAILED
    elif "configuration" in text or "config" in name:
        code = ErrorCode.CONFIG_INVALID
    elif "validation" in text or "contract" in text:
        code = ErrorCode.VALIDATION_FAILED
    elif "render" in text or "ffmpeg" in text:
        code = ErrorCode.RENDER_FAILED
    elif isinstance(exc, (ValueError, TypeError)):
        code = ErrorCode.INPUT_INVALID
    else:
        code = ErrorCode.INTERNAL

    return JobError(code=code, message=message, retryable=is_retryable_error(exc))


__all__ = ["ErrorCode", "JobError", "classify_exception"]
