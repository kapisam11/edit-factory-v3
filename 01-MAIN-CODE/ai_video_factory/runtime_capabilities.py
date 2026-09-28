"""Centralized optional-runtime capability detection and actionable errors."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import shutil


@dataclass(frozen=True)
class CapabilityStatus:
    key: str
    available: bool
    requirement: str
    detail: str

    def require(self) -> None:
        if not self.available:
            raise RuntimeError(f"Feature '{self.key}' requires {self.requirement}. Install the appropriate optional extra to enable it.")


def _module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def capabilities() -> dict[str, CapabilityStatus]:
    vision = _module("cv2")
    return {
        "vision": CapabilityStatus("vision", vision, "the 'vision' extra (opencv-python-headless)", "OpenCV frame analysis"),
        "ocr": CapabilityStatus("ocr", vision and _module("pytesseract") and shutil.which("tesseract") is not None, "the 'ocr' extra and the tesseract executable", "OCR scene analysis"),
        "audio_analysis": CapabilityStatus("audio_analysis", _module("librosa"), "the 'beats' or 'full' extra", "music and beat analysis"),
        "embeddings": CapabilityStatus("embeddings", _module("sentence_transformers"), "the 'intelligence' or 'full' extra", "semantic similarity"),
        "diarization": CapabilityStatus("diarization", _module("pyannote.audio"), "the diarization/full extra plus a provider token", "speaker diarization"),
        "groq": CapabilityStatus("groq", _module("groq"), "the groq extra plus GROQ_API_KEY", "Groq AI provider"),
        "ffmpeg": CapabilityStatus("ffmpeg", shutil.which("ffmpeg") is not None, "FFmpeg installed on PATH", "media rendering"),
        "ffprobe": CapabilityStatus("ffprobe", shutil.which("ffprobe") is not None, "FFmpeg/FFprobe installed on PATH", "media validation"),
    }


def require(name: str) -> None:
    try:
        status = capabilities()[name]
    except KeyError as exc:
        raise ValueError(f"Unknown runtime capability: {name}") from exc
    status.require()


__all__ = ["CapabilityStatus", "capabilities", "require"]
