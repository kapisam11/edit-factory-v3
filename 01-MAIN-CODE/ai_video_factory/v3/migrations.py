"""Explicit V3 artifact migrations."""
from __future__ import annotations

from typing import Any, Callable, Mapping

CURRENT_VERSION = "3.0.1"
Migration = Callable[[dict[str, Any]], dict[str, Any]]


def _3_0_0_to_3_0_1(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["schema_version"] = CURRENT_VERSION
    result.setdefault("migration_history", [])
    history = list(result["migration_history"]) if isinstance(result["migration_history"], list) else []
    history.append({"from": "3.0.0", "to": "3.0.1", "migration": "externalize_source_metadata"})
    result["migration_history"] = history
    result.pop("source_metadata", None)
    for hook in result.get("hooks", []):
        if isinstance(hook, dict):
            hook.setdefault("score_semantics", "legacy_template_priority")
            hook.setdefault(
                "evaluation",
                {
                    "evaluator": "legacy_template_priority",
                    "version": "0.0.0",
                    "score": float(hook.get("score", 0.0)),
                    "recomputed": False,
                },
            )
    score_bundle = result.get("score_bundle")
    if isinstance(score_bundle, dict):
        score_bundle["technical_validity"] = None
    return result


def _3_0_1_identity(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["schema_version"] = CURRENT_VERSION
    result.pop("source_metadata", None)
    return result


MIGRATIONS: Mapping[tuple[str, str], Migration] = {
    ("3.0.0", "3.0.1"): _3_0_0_to_3_0_1,
    ("3.0.1", "3.0.1"): _3_0_1_identity,
}


def migrate_to_current(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    source = str(data.get("schema_version") or data.get("version") or "3.0.0")
    if source == CURRENT_VERSION:
        return MIGRATIONS[(CURRENT_VERSION, CURRENT_VERSION)](data)
    if (source, CURRENT_VERSION) in MIGRATIONS:
        return MIGRATIONS[(source, CURRENT_VERSION)](data)
    raise ValueError(
        f"unsupported schema version {source}: no migration is registered to {CURRENT_VERSION}"
    )


__all__ = ["CURRENT_VERSION", "MIGRATIONS", "migrate_to_current"]
