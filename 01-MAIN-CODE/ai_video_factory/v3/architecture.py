"""Separated V3 domain views for creative, platform, editorial, performance and production state."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class CreativeBlueprint:
    core_idea: object
    edit_type: str
    hooks: tuple[object, ...]
    clip_plan: tuple[object, ...]
    music: object
    retention: tuple[object, ...]


@dataclass(frozen=True)
class PlatformPackage:
    platform: str
    variants: Mapping[str, Mapping[str, object]]
    packaging: object
    safe_area: Mapping[str, object]


@dataclass(frozen=True)
class EditorialAssessment:
    quality: object
    decisions: tuple[object, ...]
    evidence: tuple[object, ...] = ()


@dataclass(frozen=True)
class PerformanceAssessment:
    heuristics: Mapping[str, float]
    metadata: object


@dataclass(frozen=True)
class ProductionContract:
    qc: object


@dataclass(frozen=True)
class V3JobArtifact:
    creative: CreativeBlueprint
    platform: PlatformPackage
    editorial: EditorialAssessment
    performance: PerformanceAssessment
    production: ProductionContract
    provenance: Mapping[str, object] = field(default_factory=dict)


__all__ = [
    "CreativeBlueprint",
    "PlatformPackage",
    "EditorialAssessment",
    "PerformanceAssessment",
    "ProductionContract",
    "V3JobArtifact",
]
