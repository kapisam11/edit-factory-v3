"""Explicit V3-to-legacy renderer adapter.

V3 code depends on this narrow bridge instead of importing the legacy renderer
implementation throughout the planning/contract layer. The bridge is the one
compatibility seam that remains intentionally versioned and testable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from .production_models import ProductionResult
from .production_pipeline import run_production_pipeline
from .v3_engine import V3Blueprint
from .v3.effects import EffectCompiler
from .v3.production_spec import ProductionSpec


@dataclass(frozen=True)
class V3RenderPlan:
    """Typed rendering instructions derived from one validated V3 blueprint."""
    target_seconds: float
    platform: str
    edit_type: str
    clip_plan: tuple[Mapping[str, Any], ...]
    hooks: tuple[Mapping[str, Any], ...]
    retention_map: tuple[Mapping[str, Any], ...]
    platform_profile: Mapping[str, Any]
    production_spec: Mapping[str, Any] = field(default_factory=dict)
    render_ir: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_blueprint(cls, blueprint: V3Blueprint, *, include_retention: bool = True) -> "V3RenderPlan":
        payload = blueprint.to_dict()
        retention = tuple(dict(item) for item in payload["retention_map"]) if include_retention else tuple()
        render_ir = EffectCompiler().compile(retention)
        spec = ProductionSpec.from_values(blueprint.duration, blueprint.platform)
        return cls(
            target_seconds=spec.duration,
            platform=blueprint.platform,
            edit_type=blueprint.edit_type,
            clip_plan=tuple(dict(item) for item in payload["clip_plan"]),
            hooks=tuple(dict(item) for item in payload["hooks"]),
            retention_map=retention,
            platform_profile=asdict(blueprint.platform_constraints),
            production_spec=spec.to_dict(),
            render_ir=render_ir.to_dict(),
        )

    def to_directives(self) -> dict[str, Any]:
        return {
            "edit_type": self.edit_type,
            "clip_plan": [dict(item) for item in self.clip_plan],
            "hooks": [dict(item) for item in self.hooks],
            "retention_map": [dict(item) for item in self.retention_map],
            "platform": self.platform,
            "platform_profile": dict(self.platform_profile),
            "production_spec": dict(self.production_spec),
            "render_ir": dict(self.render_ir),
            "blueprint_contract": "3.0.0",
        }

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
    render_plan: Optional[V3RenderPlan] = None


def render_v3(request: V3RenderRequest) -> ProductionResult:
    """Render one V3 contract through the isolated compatibility bridge."""
    return run_production_pipeline(
        request.input_video,
        request.topic,
        request.package_dir,
        target_seconds=request.target_seconds,
        research_summary={**dict(request.research_summary), **({"v3_render_plan": request.render_plan.to_directives()} if request.render_plan else {})},
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


__all__=["V3RenderPlan","V3RenderRequest","render_v3"]
