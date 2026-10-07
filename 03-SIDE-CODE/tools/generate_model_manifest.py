#!/usr/bin/env python3
"""Create a SHA-256 manifest for the immutable production model bundle."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: generate_model_manifest.py <model-dir> <immutable-commit>")
    root = Path(sys.argv[1]).resolve()
    commit = sys.argv[2].strip().lower()
    if not root.is_dir() or len(commit) != 40:
        raise SystemExit("invalid model directory or immutable commit")
    assets = []
    for path in (root / "deploy.prototxt", root / "mobilenet.caffemodel"):
        if not path.is_file() or path.stat().st_size <= 0:
            raise SystemExit(f"missing model asset: {path}")
        assets.append({
            "name": path.name,
            "sha256": sha256(path),
            "size_bytes": path.stat().st_size,
        })
    (root / "model-manifest.json").write_text(
        json.dumps({
            "format_version": 1,
            "source": "MobileNet-SSD",
            "immutable_commit": commit,
            "assets": assets,
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
