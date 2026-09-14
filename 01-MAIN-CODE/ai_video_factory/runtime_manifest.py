"""Runtime capability detection and reproducible asset/tool manifest."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import os
from typing import Any, Dict

from .asset_manager import RUNTIME_ASSETS, install_runtime_assets, resolve_binary, runtime_asset_path


def _module_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def build_runtime_manifest() -> Dict[str, Any]:
    def module_available(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            return False

    assets = install_runtime_assets(download_missing=False)
    ffmpeg = resolve_binary("ffmpeg")
    ffprobe = resolve_binary("ffprobe")
    return {
        "schema_version": 2,
        "ffmpeg": {"available": bool(ffmpeg), "path": ffmpeg},
        "ffprobe": {"available": bool(ffprobe), "path": ffprobe},
        "models": {
            "mobilenet_ssd": {
                "available": all(bool(item["available"]) for item in assets.values()),
                "assets": assets,
            }
        },
        "python": {
            "opencv": {"available": module_available("cv2"), "version": _module_version("opencv-python-headless") or _module_version("opencv-python")},
            "pytesseract": {"available": module_available("pytesseract"), "version": _module_version("pytesseract")},
            "librosa": {"available": module_available("librosa"), "version": _module_version("librosa")},
            "pyannote_audio": {"available": module_available("pyannote.audio"), "version": _module_version("pyannote.audio")},
            "edge_tts": {"available": module_available("edge_tts"), "version": _module_version("edge-tts")},
        },
        "environment": {
            "huggingface_token": bool(os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("PYANNOTE_AUTH_TOKEN")),
            "openai_key": bool(os.environ.get("OPENAI_API_KEY")),
            "groq_key": bool(os.environ.get("GROQ_API_KEY")),
            "elevenlabs_key": bool(os.environ.get("ELEVENLABS_API_KEY")),
        },
        "asset_specs": [
            {
                "name": spec.name,
                "relative_path": spec.relative_path,
                "url": spec.url,
                "sha256_pinned": bool(spec.sha256),
                "path": str(runtime_asset_path(spec)),
            }
            for spec in RUNTIME_ASSETS
        ],
    }
