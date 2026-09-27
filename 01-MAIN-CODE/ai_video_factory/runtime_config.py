"""Centralized, typed runtime configuration for high-value execution paths."""
from __future__ import annotations

from dataclasses import dataclass
import os


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class RuntimeConfig:
    ffmpeg_timeout_seconds: int = 3600
    ffprobe_timeout_seconds: int = 30
    model_timeout_seconds: int = 30
    openai_model: str = "gpt-4o-mini"
    groq_model: str = "llama-3.3-70b-versatile"
    cleanup_interval_seconds: int = 21600
    retention_days: int = 7

    @classmethod
    def from_environment(cls) -> "RuntimeConfig":
        return cls(
            ffmpeg_timeout_seconds=_int("AIVF_FFMPEG_TIMEOUT_SECONDS", 3600, 1, 7200),
            ffprobe_timeout_seconds=_int("AIVF_FFPROBE_TIMEOUT_SECONDS", 30, 1, 7200),
            model_timeout_seconds=_int("AIVF_MODEL_TIMEOUT_SECONDS", 30, 1, 300),
            openai_model=os.environ.get("AIVF_OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
            groq_model=os.environ.get("AIVF_GROQ_MODEL", "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile",
            cleanup_interval_seconds=_int("AIVF_CLEANUP_INTERVAL_SECONDS", 21600, 60, 7 * 86400),
            retention_days=_int("AIVF_RETENTION_DAYS", 7, 1, 3650),
        )


def runtime_config() -> RuntimeConfig:
    return RuntimeConfig.from_environment()


__all__ = ["RuntimeConfig", "runtime_config"]
