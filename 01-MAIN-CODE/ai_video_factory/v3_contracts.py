"""Canonical V3 request contract shared by CLI, dashboard, and pipeline code."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .v3_engine import V3Config
from .v3_exceptions import V3InputError


@dataclass(frozen=True)
class V3Request:
    input_video: str
    topic: str
    package_dir: str
    context: str = ""
    target_seconds: float = 30.0
    platform: str = "youtube_shorts"
    audience: str = "general short-form viewers"
    bpm: int = 120
    edit_type: str | None = None
    model_key: str | None = None
    skip_qc: bool = False
    music_path: str | None = None
    enable_ocr: bool = False
    enable_object_detection: bool = True
    enable_diarization: bool = False
    diarization_token: str | None = None
    seed: int = 0

    def config(self) -> V3Config:
        return V3Config(
            target_seconds=float(self.target_seconds),
            platform=self.platform,
            audience=self.audience,
            bpm=self.bpm,
            language="en",
            seed=self.seed,
        )

    def validate(self, *, require_source_file: bool = True) -> None:
        if require_source_file:
            source = Path(self.input_video)
            if not source.is_file():
                raise V3InputError(f"V3 input video is missing or not a file: {source}")
            if source.stat().st_size <= 0:
                raise V3InputError("V3 input video is empty")

        topic = str(self.topic).strip()
        audience = str(self.audience).strip()
        package = str(self.package_dir).strip()
        if not topic or len(topic) > 500:
            raise V3InputError("topic must be non-empty and <= 500 characters")
        if not audience or len(audience) > 500:
            raise V3InputError("audience must be non-empty and <= 500 characters")
        if not package:
            raise V3InputError("package_dir is required")

        try:
            self.config().validate()
        except (TypeError, ValueError, OverflowError) as exc:
            raise V3InputError(str(exc)) from exc

        if self.context and len(str(self.context)) > 2000:
            raise V3InputError("context must be <= 2000 characters")


__all__ = ["V3Request"]
