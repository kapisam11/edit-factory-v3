"""Traceable creative provenance for reproducible V3 decisions."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any,Mapping
from ..idempotency import stable_hash

@dataclass(frozen=True)
class CreativeProvenance:
    pipeline_version: str
    planner_version: str
    retention_policy_version: str
    caption_policy_version: str
    scoring_version: str
    prompt_template_version: str
    model_version: str
    configuration_hash: str
    source_hash: str
    blueprint_hash: str
    renderer_version: str
    platform_policy_version: str

    def to_dict(self)->dict[str,Any]:
        return {
            "schema_version":"1.0.0",
            "pipeline_version":self.pipeline_version,
            "planner_version":self.planner_version,
            "retention_policy_version":self.retention_policy_version,
            "caption_policy_version":self.caption_policy_version,
            "scoring_version":self.scoring_version,
            "prompt_template_version":self.prompt_template_version,
            "model_version":self.model_version,
            "configuration_hash":self.configuration_hash,
            "source_hash":self.source_hash,
            "blueprint_hash":self.blueprint_hash,
            "renderer_version":self.renderer_version,
            "platform_policy_version":self.platform_policy_version,
        }

def build_creative_provenance(*,pipeline_version:str,planner_version:str,retention_policy_version:str,caption_policy_version:str,scoring_version:str,prompt_template_version:str,model_version:str,configuration:Mapping[str,Any],source_hash:str,blueprint_payload:Mapping[str,Any],renderer_version:str,platform_policy_version:str)->CreativeProvenance:
    return CreativeProvenance(
        pipeline_version=str(pipeline_version),
        planner_version=str(planner_version),
        retention_policy_version=str(retention_policy_version),
        caption_policy_version=str(caption_policy_version),
        scoring_version=str(scoring_version),
        prompt_template_version=str(prompt_template_version),
        model_version=str(model_version or "deterministic"),
        configuration_hash=stable_hash(configuration),
        source_hash=str(source_hash),
        blueprint_hash=stable_hash(dict(blueprint_payload)),
        renderer_version=str(renderer_version),
        platform_policy_version=str(platform_policy_version),
    )

__all__=["CreativeProvenance","build_creative_provenance"]
