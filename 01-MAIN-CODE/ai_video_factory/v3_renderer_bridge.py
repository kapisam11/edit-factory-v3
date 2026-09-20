"""Explicit V3-to-legacy renderer adapter.

V3 code depends on this narrow bridge instead of importing the legacy renderer
implementation throughout the planning/contract layer. The bridge is the one
compatibility seam that remains intentionally versioned and testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .production_models import ProductionResult
from .production_pipeline import run_production_pipeline


@dataclass(frozen=True)
class V3RenderRequest:
    input_video: str
    topic: str
    package_dir: str
    target_seconds: float
    research_summary: Mapping[str, Any]
    model_key: Optional[str] = None
    skip_qc: bool = False
    music_path: Optional[str] = None
    enable_ocr: bool = False
    enable_object_detection: bool = True
    enable_diarization: bool = False
    diarization_token: Optional[str] = None
    platform: str = "youtube_shorts"


def render_v3(request: V3RenderRequest) -> ProductionResult:
    """Render one V3 contract through the isolated compatibility bridge."""
    return run_production_pipeline(
        request.input_video,
        request.topic,
        request.package_dir,
        target_seconds=request.target_seconds,
        research_summary=dict(request.research_summary),
        enable_ocr=request.enable_ocr,
        model_key=request.model_key,
        skip_qc=request.skip_qc,
        music_path=request.music_path,
        enable_object_detection=request.enable_object_detection,
        enable_diarization=request.enable_diarization,
        diarization_token=request.diarization_token,
        platform=request.platform,
        allow_auto_fix=False,
        finalize_upload_package=False,
    )


__all__=["V3RenderRequest","render_v3"]
