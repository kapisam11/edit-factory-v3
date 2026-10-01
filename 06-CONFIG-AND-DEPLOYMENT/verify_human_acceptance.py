#!/usr/bin/env python3
"""Validate a signed-off human media acceptance record against a package manifest."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the verifier runnable directly from a source checkout as well as an
# installed environment; do not require callers to set PYTHONPATH manually.
_MAIN_CODE = Path(__file__).resolve().parents[1] / "01-MAIN-CODE"
if _MAIN_CODE.is_dir() and str(_MAIN_CODE) not in sys.path:
    sys.path.insert(0, str(_MAIN_CODE))

from ai_video_factory.production_assurance import verify_artifact_manifest

SHA256 = re.compile(r"^[0-9a-f]{64}$", re.I)


def die(message: str) -> "NoReturn":
    raise SystemExit(message)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        die("usage: verify_human_acceptance.py <package_dir> <acceptance.json>")

    package = Path(argv[1]).resolve()
    acceptance_path = Path(argv[2]).resolve()
    manifest_path = package / "artifact_manifest.json"

    if not package.is_dir():
        die(f"package not found: {package}")
    if not acceptance_path.is_file():
        die(f"acceptance record not found: {acceptance_path}")
    if not manifest_path.is_file():
        die(f"artifact manifest not found: {manifest_path}")

    try:
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die(f"invalid JSON input: {exc}")

    if acceptance.get("schema_version") != 1:
        die("unsupported acceptance schema")
    if str(acceptance.get("package_name") or "") != package.name:
        die("acceptance package_name does not match package")
    if not all(bool(acceptance.get(key)) for key in ("video_reviewed", "audio_reviewed", "rights_reviewed")):
        die("video/audio/rights human review must all be explicitly true")
    reviewer = str(acceptance.get("reviewer") or "").strip()
    if len(reviewer) < 2:
        die("reviewer is required")
    try:
        reviewed_at = datetime.fromisoformat(str(acceptance.get("reviewed_at")).replace("Z", "+00:00"))
    except ValueError:
        die("reviewed_at must be a valid ISO-8601 timestamp")
    if reviewed_at.tzinfo is None:
        die("reviewed_at must include a timezone")
    if reviewed_at.astimezone(timezone.utc) > datetime.now(timezone.utc):
        die("reviewed_at cannot be in the future")

    manifest_hash = str(manifest.get("manifest_sha256") or "").lower()
    supplied = str(acceptance.get("artifact_manifest_sha256") or "").lower()
    if not SHA256.fullmatch(manifest_hash) or manifest_hash != supplied:
        die("acceptance artifact hash does not match artifact_manifest.json")

    # The stored manifest hash alone is insufficient: it can still describe
    # files that were modified after the human review. Re-hash the package now.
    integrity = verify_artifact_manifest(package, manifest)
    if not integrity.get("ok"):
        die("accepted package no longer matches its artifact manifest: " + "; ".join(list(integrity.get("errors") or [])[:10]))

    result = {
        "ok": True,
        "package": package.name,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at.astimezone(timezone.utc).isoformat(),
        "artifact_manifest_sha256": manifest_hash,
        "human_review_complete": True,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
