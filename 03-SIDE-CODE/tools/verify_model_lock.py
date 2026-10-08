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
        try:
            path = (base / relative).resolve()
        except OSError:
            path = base / "__invalid_model_path__"
        if base not in path.parents:
            results.append({"name": name, "path": str(path), "ok": False, "error": "model path escapes repository root"})
            continue
        env_name = str(asset["sha256_env"])
        locked_expected = str(asset.get("sha256", "")).strip().lower()
        env_expected = os.environ.get(env_name, "").strip().lower()
        expected = locked_expected or env_expected
        source = "model-lock.json" if locked_expected else ("environment" if env_expected else "")
        actual = sha256(path) if path.is_file() else ""
        lock_ok = len(locked_expected) == 64 and all(ch in "0123456789abcdef" for ch in locked_expected)
        immutable_commit = str(payload.get("immutable_commit", "")).strip().lower()
        url = str(asset.get("url", "")).strip()
        immutable_url_ok = bool(immutable_commit) and immutable_commit in url
        results.append({
            "name": name,
            "path": str(path),
            "sha256_env": env_name,
            "expected": expected,
            "expected_source": source,
            "actual": actual,
            "lock_sha256_present": lock_ok,
            "immutable_url": immutable_url_ok,
            "ok": lock_ok and immutable_url_ok and bool(actual) and actual == expected,
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
