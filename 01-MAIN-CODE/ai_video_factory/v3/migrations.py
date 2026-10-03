"""Explicit V3 artifact migrations."""
from __future__ import annotations
from typing import Any,Mapping,Callable

Migration=Callable[[dict[str,Any]],dict[str,Any]]
CURRENT_VERSION="3.0.0"

def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.setdefault("schema_version", result.get("version", CURRENT_VERSION))
    # source_metadata was historically embedded in V3 blueprints. It is now
    # externalized into source_manifest.json while old blueprints remain readable.
    result.pop("source_metadata", None)
    return result

MIGRATIONS: Mapping[tuple[str,str],Migration]={
    ("3.0.0","3.0.0"):_identity,
}

def migrate_to_current(payload:Mapping[str,Any])->dict[str,Any]:
    data=dict(payload)
    source=str(data.get("schema_version") or data.get("version") or CURRENT_VERSION)
    if source==CURRENT_VERSION:
        return _identity(data)
    raise ValueError(
        f"no migration is registered from blueprint schema {source} to {CURRENT_VERSION}"
    )

__all__=["CURRENT_VERSION","MIGRATIONS","migrate_to_current"]
