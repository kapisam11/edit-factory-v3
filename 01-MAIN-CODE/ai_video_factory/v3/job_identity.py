"""Content-addressed V3 job identity and configuration fingerprints."""
from __future__ import annotations
from typing import Any, Mapping
from ..idempotency import stable_hash


def configuration_hash(configuration: Mapping[str, Any]) -> str:
    return stable_hash(dict(configuration))


def job_identity(
    *,
    source_hash: str,
    blueprint_hash: str,
    config_hash: str,
    renderer_version: str,
    platform_policy_version: str,
) -> str:
    digest = stable_hash({
        "source_hash": source_hash,
        "blueprint_hash": blueprint_hash,
        "config_hash": config_hash,
        "renderer_version": renderer_version,
        "platform_policy_version": platform_policy_version,
    })
    return f"job-{digest[:32]}"


__all__ = ["configuration_hash", "job_identity"]
