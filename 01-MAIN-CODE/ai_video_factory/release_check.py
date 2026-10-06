"""Strict release gate separate from the informational readiness report."""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from .db_migrations import SchemaMismatch, verify_database_schema


class ReleaseCheckError(RuntimeError):
    pass


def _require(name: str, value: object) -> None:
    if value is None or not str(value).strip():
        raise ReleaseCheckError(f"required release control missing: {name}")


def check_release(root: str | Path = ".") -> dict[str, object]:
    base = Path(root).resolve()
    checks: dict[str, dict[str, object]] = {}

    ci = os.environ.get("AIVF_CI_STATUS", "").strip().lower()
    checks["ci_green"] = {"ok": ci == "green", "detail": ci or "unset"}

    security = os.environ.get("AIVF_SECURITY_STATUS", "").strip().lower()
    checks["security_green"] = {"ok": security == "green", "detail": security or "unset"}

    e2e = os.environ.get("AIVF_E2E_STATUS", "").strip().lower()
    checks["e2e_green"] = {"ok": e2e == "green", "detail": e2e or "unset"}

    human = os.environ.get("AIVF_HUMAN_APPROVAL", "").strip().lower()
    checks["human_approval"] = {"ok": human in {"approved", "true", "1"}, "detail": human or "unset"}

    image = os.environ.get("AIVF_IMAGE", "").strip()
    checks["image_pinned"] = {
        "ok": bool(re.fullmatch(r".+@sha256:[0-9a-f]{64}", image)),
        "detail": image or "unset",
    }

    model_hashes = [
        os.environ.get("AIVF_MOBILENET_CONFIG_SHA256", "").strip(),
        os.environ.get("AIVF_MOBILENET_WEIGHTS_SHA256", "").strip(),
    ]
    checks["model_hashes_pinned"] = {
        "ok": all(re.fullmatch(r"[0-9a-fA-F]{64}", value or "") for value in model_hashes),
        "detail": "configured" if all(model_hashes) else "missing",
    }

    db_path = Path(os.environ.get("AIVF_DB_PATH", str(base / "state" / "jobs.db")))
    try:
        verify_database_schema(db_path)
    except (SchemaMismatch, OSError, ValueError) as exc:
        checks["database_migrated"] = {"ok": False, "detail": str(exc)}
    else:
        checks["database_migrated"] = {"ok": True, "detail": str(db_path)}

    backup_marker = Path(
        os.environ.get("AIVF_BACKUP_RESTORE_MARKER", str(base / "state" / "backup-restore-verified"))
    )
    checks["backup_restore_verified"] = {
        "ok": backup_marker.is_file(),
        "detail": str(backup_marker),
    }

    bad = [name for name, data in checks.items() if not bool(data["ok"])]
    return {"ok": not bad, "failures": bad, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Strict Edit Factory production release gate")
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args(argv)
    result = check_release(args.root)
    for name, check in result["checks"].items():
        print(f"{name}: {'PASS' if check['ok'] else 'BLOCKED'} — {check['detail']}")
    if result["failures"]:
        print("release blocked: " + ", ".join(result["failures"]))
        return 1
    print("release gate: PASS")
    return 0


__all__ = ["ReleaseCheckError", "check_release", "main"]
