"""Separated V3 domain views over the compatibility blueprint."""
from __future__ import annotations
from dataclasses import dataclass,field
from typing import Any,Mapping

@dataclass(frozen=True)
class CreativeBlueprint:
    core_idea: Any
    edit_type: str
    hooks: tuple[Any,...]
    clip_plan: tuple[Any,...]
    music: Any
    retention: tuple[Any,...]

@dataclass(frozen=True)
class PlatformPackage:
    platform: str
    variants: Mapping[str,Mapping[str,Any]]
    packaging: Any
    safe_area: Mapping[str,Any]

@dataclass(frozen=True)
class EditorialAssessment:
    quality: Any
    decisions: tuple[Any,...]
    evidence: tuple[Any,...]=()

@dataclass(frozen=True)
class PerformanceAssessment:
    heuristics: Mapping[str,float]
    metadata: Any

@dataclass(frozen=True)
class ProductionContract:
    qc: Any

@dataclass(frozen=True)
class V3JobArtifact:
    creative: CreativeBlueprint
    platform: PlatformPackage
    editorial: EditorialAssessment
    performance: PerformanceAssessment
    production: ProductionContract
    provenance: Mapping[str,Any]=field(default_factory=dict)

__all__=["CreativeBlueprint","PlatformPackage","EditorialAssessment","PerformanceAssessment","ProductionContract","V3JobArtifact"]
