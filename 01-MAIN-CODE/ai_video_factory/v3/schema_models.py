"""Strict Pydantic boundary for persisted V3 blueprint payloads.

The existing dataclasses remain the compatibility/runtime domain objects. This
module owns external JSON validation so malformed payloads are rejected before
manual domain reconstruction happens.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CoreIdeaModel(StrictModel):
    topic: str
    why_people_care: str
    emotional_angle: str
    target_emotion: str
    watch_to_end_reason: str
    payoff: str
    stakes: str


class HookModel(StrictModel):
    visual: str
    text: str
    emotional: str
    score: float
    evaluation: dict[str, Any] = Field(default_factory=dict)
    score_semantics: str = "legacy_template_priority"


class ClipEvidenceModel(StrictModel):
    source_asset: str = ""
    source_start: float = 0.0
    source_end: float = 0.0
    semantic_tags: tuple[str, ...] = ()
    evidence_score: float = 0.0
    evidence_status: str = "planned"


class ClipModel(StrictModel):
    index: int
    start: float
    end: float
    purpose: str
    emotion: str
    visual_style: str
    text_overlay: str
    camera_motion: str
    transition: str
    evidence: ClipEvidenceModel = Field(default_factory=ClipEvidenceModel)


class MusicModel(StrictModel):
    bpm: int
    energy: str
    emotional_tone: str
    beat_seconds: float
    drop_time: float
    sync_points: tuple[float, ...]


class RetentionModel(StrictModel):
    time: float
    kind: str
    instruction: str
    reason: str = "ATTENTION_RISK"
    semantic_importance: float = 0.5
    confidence: float = 0.5


class QualityModel(StrictModel):
    passed: bool
    score: int
    checks: dict[str, bool] = {}
    warnings: tuple[str, ...] = ()


class ScoreBundleModel(StrictModel):
    technical_validity: float | None = None
    creative_quality: float = 0.0
    performance_heuristic: float = 0.0
    creative_quality_confidence: float = 0.0
    performance_confidence: float = 0.0
    calibration_status: str = "uncalibrated"
    technical_scope: str = "blueprint contract"
    creative_scope: str = "editorial heuristic"


class BlueprintPayloadModel(StrictModel):
    version: str
    schema_version: str
    core_idea: CoreIdeaModel
    edit_type: str
    hooks: tuple[HookModel, ...]
    clip_plan: tuple[ClipModel, ...]
    music: MusicModel
    retention_map: tuple[RetentionModel, ...]
    thumbnail_concept: str
    title_options: tuple[str, ...]
    hashtags: tuple[str, ...]
    description: str
    platform_variants: dict[str, dict[str, Any]]
    quality: QualityModel
    metrics: dict[str, float]
    capabilities: tuple[str, ...]
    platform: str = "youtube_shorts"
    audience: str = "general short-form viewers"
    platform_profile: dict[str, Any] | None = None
    qc: dict[str, Any] = {}
    packaging: dict[str, Any] = {}
    metric_metadata: dict[str, Any] = {}
    score_bundle: ScoreBundleModel | None = None
    editorial_decisions: tuple[dict[str, Any], ...] = ()
    editorial_evidence_summary: dict[str, Any] | None = None
    migration_history: tuple[dict[str, Any], ...] = ()


def validate_blueprint_model(payload: dict[str, Any]) -> dict[str, Any]:
    model = BlueprintPayloadModel.model_validate(payload)
    return model.model_dump(mode="python")


__all__ = ["BlueprintPayloadModel", "validate_blueprint_model"]
