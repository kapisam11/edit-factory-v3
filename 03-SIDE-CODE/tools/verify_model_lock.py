#!/usr/bin/env python3
"""Verify preinstalled production model bytes against the immutable model lock."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(root: str | Path = ".") -> dict[str, object]:
    base = Path(root).resolve()
    lock_path = Path(os.environ.get(
        "AIVF_MODEL_LOCK_FILE",
        str(base / "06-CONFIG-AND-DEPLOYMENT" / "model-lock.json"),
    ))
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    results = []
    for asset in payload.get("assets", []):
        name = str(asset["name"])
        relative = str(asset["relative_path"])
        env_name = str(asset["sha256_env"])
        path = base / relative
        expected = os.environ.get(env_name, "").strip().lower()
        manifest = path.parent / "model-manifest.json"
        if not expected and manifest.is_file():
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                expected = next(
                    (str(item.get("sha256", "")).strip().lower()
                     for item in payload.get("assets", [])
                     if str(item.get("name")) == path.name),
                    "",
                )
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                expected = ""
        actual = sha256(path) if path.is_file() else ""
        results.append({
            "name": name,
            "path": str(path),
            "sha256_env": env_name,
            "expected": expected,
            "actual": actual,
            "ok": bool(expected) and actual == expected,
        })
    return {"ok": all(item["ok"] for item in results), "assets": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    result = verify(args.root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
