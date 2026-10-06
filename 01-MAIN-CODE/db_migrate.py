"""Explicit database migration command for production deployment."""
from __future__ import annotations

import os
from pathlib import Path

from dashboard_store import DashboardStore
from ai_video_factory.db_migrations import CURRENT_SCHEMA_VERSION


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    raw = args[0] if args and not args[0].startswith("-") else os.environ.get("AIVF_STATE_DIR", "state")
    base = Path(raw)
    db = base / "jobs.db" if base.is_dir() or base.suffix == "" else base

    store = DashboardStore(db)
    version = store.ensure_indexes()
    # ensure_indexes is the explicit migration/bootstrap command; verify after
    # migration so a corrupt or partially applied schema cannot be reported green.
    verified = store.verify_schema()
    print(f"database={db}")
    print(f"schema_version={verified}")
    print(f"required_schema_version={CURRENT_SCHEMA_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
