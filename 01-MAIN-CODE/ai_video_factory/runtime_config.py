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
    edge_tts_timeout_seconds: int = 120
    pyttsx3_timeout_seconds: int = 120
    elevenlabs_timeout_seconds: int = 60
    max_job_retries: int = 3
    db_cleanup_interval_seconds: int = 3600
    db_retention_days: int = 30
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
            edge_tts_timeout_seconds=_int("AIVF_EDGE_TTS_TIMEOUT_SECONDS", 120, 10, 1800),
            pyttsx3_timeout_seconds=_int("AIVF_PYTTSX3_TIMEOUT_SECONDS", 120, 10, 3600),
            elevenlabs_timeout_seconds=_int("AIVF_ELEVENLABS_TIMEOUT_SECONDS", 60, 10, 1800),
            max_job_retries=_int("AIVF_MAX_JOB_RETRIES", 3, 0, 10),
            db_cleanup_interval_seconds=_int("AIVF_DB_CLEANUP_INTERVAL_SECONDS", 3600, 300, 7 * 86400),
            db_retention_days=_int("AIVF_DB_RETENTION_DAYS", 30, 1, 3650),
            openai_model=os.environ.get("AIVF_OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
            groq_model=os.environ.get("AIVF_GROQ_MODEL", "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile",
            cleanup_interval_seconds=_int("AIVF_CLEANUP_INTERVAL_SECONDS", 21600, 60, 7 * 86400),
            retention_days=_int("AIVF_RETENTION_DAYS", 7, 1, 3650),
        )


def runtime_config() -> RuntimeConfig:
    return RuntimeConfig.from_environment()


__all__ = ["RuntimeConfig", "runtime_config"]
