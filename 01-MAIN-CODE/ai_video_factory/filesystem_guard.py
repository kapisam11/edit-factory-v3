"""Fail-closed filesystem helpers for user-controlled paths."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from .production_guardrails import GuardrailError, validate_path_inside


def safe_join(root: str | Path, *parts: str | os.PathLike[str], allow_base: bool = False) -> Path:
    """Resolve a child path and reject absolute/traversal components."""
    base = Path(root).expanduser().resolve(strict=False)
    current = base
    for raw in parts:
        value = os.fspath(raw)
        if not value or "\x00" in value:
            raise GuardrailError("filesystem path component is invalid")
        component = Path(value)
        if component.is_absolute() or component.drive:
            raise GuardrailError("absolute filesystem path component is not allowed")
        current = current / component
    return validate_path_inside(base, current, allow_base=allow_base)


def safe_delete(root: str | Path, target: str | Path) -> Path:
    path = validate_path_inside(root, target)
    if path.is_dir():
        raise GuardrailError("safe_delete does not remove directories")
    path.unlink(missing_ok=True)
    return path


def safe_rmtree(root: str | Path, target: str | Path) -> Path:
    import shutil

    path = validate_path_inside(root, target)
    if path == Path(root).resolve():
        raise GuardrailError("refusing to recursively delete the root")
    shutil.rmtree(path, ignore_errors=False)
    return path


def assert_all_inside(root: str | Path, paths: Iterable[str | Path]) -> list[Path]:
    return [validate_path_inside(root, path) for path in paths]


__all__ = ["assert_all_inside", "safe_delete", "safe_join", "safe_rmtree"]
