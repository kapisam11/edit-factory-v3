#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, "/app/01-MAIN-CODE")
from ai_video_factory.backup_restore import create_backup, verify_backup, _pack_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument(
        "--staging",
        default=os.environ.get("AIVF_BACKUP_STAGING_DIR", "/app/state/.backup-staging"),
    )
    args = parser.parse_args(argv)

    archive = Path(args.archive)
    staging = Path(args.staging)
    staging.mkdir(parents=True, exist_ok=True)
    created = create_backup(
        state_dir=os.environ.get("AIVF_STATE_DIR", "/app/state"),
        database_path=os.environ.get("AIVF_DB_PATH", str(Path(os.environ.get("AIVF_STATE_DIR", "/app/state")) / "jobs.db")),
        knowledge_dir=os.environ.get("AIVF_KNOWLEDGE_DIR", "/app/knowledge_base_v3"),
        output_dir=os.environ.get("AIVF_OUTPUT_DIR", "/app/output"),
        destination=staging,
        include_output=True,
    )
    snapshot = Path(created["path"])
    try:
        verified = verify_backup(snapshot)
        if not verified["ok"]:
            raise SystemExit(f"backup verification failed: {verified}")
        written = _pack_snapshot(snapshot, archive)
        if not written.is_file():
            raise SystemExit(f"backup archive was not created: {written}")
        print(written)
        return 0
    finally:
        shutil.rmtree(snapshot, ignore_errors=True)
        try:
            staging.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
