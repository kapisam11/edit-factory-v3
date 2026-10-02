"""Content-addressed stage idempotency primitives for V3."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_hash(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage_cache_key(
    *,
    source_hash: str,
    stage_name: str,
    stage_version: str,
    configuration: Mapping[str, Any] | None = None,
) -> str:
    return stable_hash(
        {
            "source_hash": source_hash,
            "stage_name": stage_name,
            "stage_version": stage_version,
            "configuration_hash": stable_hash(configuration or {}),
        }
    )


def cache_record(
    *,
    key: str,
    stage_name: str,
    stage_version: str,
    inputs: Sequence[str],
    outputs: Sequence[str],
) -> dict[str, Any]:
    return {
        "key": key,
        "stage_name": stage_name,
        "stage_version": stage_version,
        "inputs": list(inputs),
        "outputs": list(outputs),
    }


def is_cache_hit(record: Mapping[str, Any], *, expected_key: str, package: str | Path) -> bool:
    if str(record.get("key", "")) != expected_key:
        return False
    root = Path(package)
    outputs = record.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return False
    return all((root / str(output)).is_file() for output in outputs)


__all__ = ["cache_record", "file_hash", "is_cache_hit", "stage_cache_key", "stable_hash"]
