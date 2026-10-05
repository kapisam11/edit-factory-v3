"""Modular V3 domain package.

The legacy ai_video_factory.v3_engine module remains the compatibility facade.
New code should depend on these focused modules where practical.
"""

from .architecture import (
    CreativeBlueprint,
    EditorialAssessment,
    PerformanceAssessment,
    PlatformPackage,
    ProductionContract,
    V3JobArtifact,
)
from .effects import CompiledRenderGraph, Effect, EffectCompiler, EffectKind, RenderIR
from .metadata import generate_final_metadata
from .hook_eval import HookCandidate, HookEvaluation
from .platform_policy import POLICIES, PlatformId, PlatformPolicy, get_platform_policy
from .production_spec import ProductionSpec

__all__ = [
    "CreativeBlueprint",
    "EditorialAssessment",
    "CompiledRenderGraph",
    "Effect",
    "EffectCompiler",
    "EffectKind",
    "HookCandidate",
    "HookEvaluation",
    "PerformanceAssessment",
    "POLICIES",
    "PlatformId",
    "PlatformPackage",
    "PlatformPolicy",
    "ProductionContract",
    "ProductionSpec",
    "RenderIR",
    "V3JobArtifact",
    "get_platform_policy",
    "generate_final_metadata",
]
