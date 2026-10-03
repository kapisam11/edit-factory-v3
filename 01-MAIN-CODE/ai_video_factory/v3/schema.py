"""Central V3 blueprint schema boundary."""
from __future__ import annotations
from typing import Any, Mapping

CURRENT_SCHEMA_VERSION = "3.0.0"

REQUIRED_TOP_LEVEL = frozenset({
    "version","core_idea","edit_type","hooks","clip_plan","music","retention_map",
    "thumbnail_concept","title_options","hashtags","description","platform_variants",
    "quality","metrics","capabilities",
})
OPTIONAL_TOP_LEVEL = frozenset({
    "schema_version","platform","audience","platform_profile","qc","packaging",
    "metric_metadata","score_bundle","editorial_decisions","editorial_evidence_summary",
})

def validate_blueprint_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("V3 blueprint payload must be an object")
    keys=set(payload)
    missing=sorted(REQUIRED_TOP_LEVEL-keys)
    if missing:
        raise ValueError("V3 blueprint is missing required fields: "+", ".join(missing))
    unknown=sorted(keys-(REQUIRED_TOP_LEVEL|OPTIONAL_TOP_LEVEL))
    if unknown:
        raise ValueError("V3 blueprint contains unknown fields: "+", ".join(unknown))
    version=str(payload.get("version",""))
    schema_version=str(payload.get("schema_version",version or CURRENT_SCHEMA_VERSION))
    if version != CURRENT_SCHEMA_VERSION or schema_version != CURRENT_SCHEMA_VERSION:
        raise ValueError(f"unsupported V3 blueprint schema version: {version}/{schema_version}")
    return dict(payload)

__all__=["CURRENT_SCHEMA_VERSION","OPTIONAL_TOP_LEVEL","REQUIRED_TOP_LEVEL","validate_blueprint_payload"]
