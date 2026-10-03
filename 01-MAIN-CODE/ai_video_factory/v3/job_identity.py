"""Content-addressed V3 job identity and configuration fingerprints."""
from __future__ import annotations
from typing import Any, Mapping
from . import _safe_hash


def configuration_hash(configuration: Mapping[str, Any]) -> str:
    return _safe_hash(dict(configuration))


def job_identity(
    *,
    source_hash: str,
    blueprint_hash: str,
    config_hash: str,
    renderer_version: str,
    platform_policy_version: str,
) -> str:
    digest = _safe_hash({
        "source_hash": source_hash,
        "blueprint_hash": blueprint_hash,
        "config_hash": config_hash,
        "renderer_version": renderer_version,
        "platform_policy_version": platform_policy_version,
    })
    return f"job-{digest[:32]}"


__all__ = ["configuration_hash", "job_identity"]
