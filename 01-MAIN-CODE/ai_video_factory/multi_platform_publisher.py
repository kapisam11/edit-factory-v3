"""CLI entry point for the durable multi-platform publishing queue."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Optional

from .publishing_queue import (
    PublisherConfig,
    PublisherService,
    PublisherStore,
    approve_video,
    configured_platforms,
    parse_destinations,
)

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely publish approved existing videos to YouTube, Instagram Reels and TikTok")
    parser.add_argument("--watch-dir", default=None, help="folder to watch for existing videos")
    parser.add_argument("--state-dir", default=None, help="persistent local state folder")
    parser.add_argument("--db", default=None, help="publisher SQLite database path")
    parser.add_argument("--settle-seconds", type=int, default=None, help="how long a file must remain unchanged")
    publishing = parser.add_mutually_exclusive_group()
    publishing.add_argument("--publish", dest="publish_enabled", action="store_true", help="enable real authenticated platform uploads")
    publishing.add_argument("--dry-run", dest="publish_enabled", action="store_false", help="scan and queue only; never contact upload endpoints")
    parser.set_defaults(publish_enabled=None)
    parser.add_argument("--allow-public", action="store_true", default=False, help="explicit opt-in to destinations configured for public publishing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="scan stable files and update the durable queue")
    sub.add_parser("status", help="show queue, destination configuration and recent outcomes")
    sub.add_parser("run-once", help="scan, reconcile remote jobs, and optionally start due uploads")
    sub.add_parser("daemon", help="run the persistent background worker")
    approve = sub.add_parser("approve", help="attest rights and disclosure flags for exact source bytes")
    approve.add_argument("file")
    approve.add_argument("--destinations", default=os.environ.get("AIVF_PUBLISH_DESTINATIONS", ""))
    ai = approve.add_mutually_exclusive_group(required=True)
    ai.add_argument("--ai-generated", dest="ai_generated", action="store_true")
    ai.add_argument("--not-ai-generated", dest="ai_generated", action="store_false")
    altered = approve.add_mutually_exclusive_group(required=True)
    altered.add_argument("--realistic-alteration", dest="realistic_alteration", action="store_true")
    altered.add_argument("--no-realistic-alteration", dest="realistic_alteration", action="store_false")
    approve.add_argument("--acknowledge-disclosure", action="store_true")
    retry = sub.add_parser("retry", help="retry a failed job only when no ambiguous remote operation is recorded")
    retry.add_argument("job_id")
    return parser

def _config(args: argparse.Namespace) -> PublisherConfig:
    config = PublisherConfig.from_environment(args.watch_dir)
    updates = {}
    if args.state_dir:
        state = Path(args.state_dir).expanduser().resolve()
        updates["state_dir"] = state
        if not args.db and not os.environ.get("AIVF_PUBLISH_DB"):
            updates["db_path"] = state / "multi-platform-publisher.sqlite"
        if not os.environ.get("AIVF_PUBLISH_STAGING_DIR"):
            updates["staging_dir"] = state / "publisher-staging"
    if args.db:
        updates["db_path"] = Path(args.db).expanduser().resolve()
    if args.settle_seconds is not None:
        if args.settle_seconds < 0:
            raise ValueError("--settle-seconds must be >= 0")
        updates["settle_seconds"] = args.settle_seconds
    if args.publish_enabled is not None:
        updates["publish_enabled"] = args.publish_enabled
    if args.allow_public:
        updates["allow_public"] = True
        os.environ["AIVF_PUBLISH_ALLOW_PUBLIC"] = "1"
    if updates:
        config = replace(config, **updates)
    config.validate_paths()
    return config

def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=os.environ.get("AIVF_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        config = _config(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    store = PublisherStore(config.db_path)
    if args.command == "approve":
        try:
            destinations = parse_destinations(args.destinations)
            sidecar = approve_video(
                args.file,
                config.watch_dir,
                destinations=destinations,
                ai_generated=bool(args.ai_generated),
                realistic_alteration=bool(args.realistic_alteration),
                disclosure_reviewed=bool(args.acknowledge_disclosure),
            )
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        print(json.dumps({"sidecar": str(sidecar), "publish_approved": True, "rights_reviewed": True, "destinations": destinations}, indent=2))
        return 0
    if args.command == "retry":
        try:
            store.retry_job(args.job_id)
        except (KeyError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps({"job_id": args.job_id, "state": "ready"}))
        return 0
    if args.command == "status":
        try:
            disk = shutil.disk_usage(config.state_dir)
            disk_report = {"free_bytes": disk.free, "total_bytes": disk.total}
        except OSError:
            disk_report = {"free_bytes": None, "total_bytes": None}
        print(json.dumps({
            "publish_enabled": config.publish_enabled,
            "public_publishing_enabled": config.allow_public,
            "configured_platforms": configured_platforms(),
            "queue_counts": store.counts(),
            "source_counts": store.source_summary(),
            "published_today_utc": store.published_today(),
            "disk": disk_report,
            "jobs": store.list_jobs(50),
        }, indent=2, sort_keys=True))
        return 0
    service = PublisherService(config)
    if args.command == "scan":
        print(json.dumps(service.scan(), indent=2, sort_keys=True))
        return 0
    if args.command == "run-once":
        print(json.dumps(service.run_once(), indent=2, sort_keys=True))
        return 0
    if args.command == "daemon":
        service.run_forever()
        return 0
    return 2

if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["main"]
