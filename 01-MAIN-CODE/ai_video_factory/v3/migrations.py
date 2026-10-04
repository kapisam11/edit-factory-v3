"""Explicit, auditable V3 artifact migrations."""
from __future__ import annotations

from typing import Any, Callable, Mapping

Migration = Callable[[dict[str, Any]], dict[str, Any]]
CURRENT_VERSION = "3.0.0"
SUPPORTED_FUTURE_VERSION = "3.1.0"


def _identity(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.setdefault("schema_version", result.get("version", CURRENT_VERSION))
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


def migrate_3_0_0_to_3_1_0(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Upgrade an artifact payload to the next V3 schema without lossy guessing."""
    result = _identity(dict(payload))
    history = list(result.get("migration_history") or [])
    history.append(
        {
            "from": "3.0.0",
            "to": SUPPORTED_FUTURE_VERSION,
            "operation": "explicit_forward_schema_migration",
            "lossless": True,
        }
    )
    result["schema_version"] = SUPPORTED_FUTURE_VERSION
    result["migration_history"] = history
    return result


MIGRATIONS: Mapping[tuple[str, str], Migration] = {
    ("3.0.0", "3.0.0"): _identity,
    ("3.0.0", "3.1.0"): migrate_3_0_0_to_3_1_0,
}


def migrate_to_version(
    payload: Mapping[str, Any],
    target_version: str,
) -> dict[str, Any]:
    data = dict(payload)
    source = str(data.get("schema_version") or data.get("version") or CURRENT_VERSION)
    target = str(target_version).strip()
    if source == target:
        return dict(data)
    migration = MIGRATIONS.get((source, target))
    if migration is None:
        raise ValueError(
            f"unsupported schema migration {source} -> {target}; "
            "no explicit migration is registered"
        )
    return migration(data)


def migrate_to_current(payload: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    source = str(data.get("schema_version") or data.get("version") or CURRENT_VERSION)
    if source == CURRENT_VERSION:
        return _identity(data)
    raise ValueError(
        f"unsupported schema version {source}: no migration is registered to {CURRENT_VERSION}"
    )


__all__ = [
    "CURRENT_VERSION",
    "MIGRATIONS",
    "SUPPORTED_FUTURE_VERSION",
    "migrate_3_0_0_to_3_1_0",
    "migrate_to_current",
    "migrate_to_version",
]
