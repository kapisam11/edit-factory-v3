#!/usr/bin/env python3
from pathlib import Path
import argparse
import os
import shutil
import sys

sys.path.insert(0, "/app/01-MAIN-CODE")
from ai_video_factory.backup_restore import create_backup, verify_backup

parser = argparse.ArgumentParser()
parser.add_argument("--archive", required=True)
args = parser.parse_args()

source_root = Path("/tmp/aivf-backup-source")
shutil.rmtree(source_root, ignore_errors=True)
source_root.mkdir(parents=True)
created = create_backup(
    state_dir=os.environ.get("AIVF_STATE_DIR", "/app/state"),
    knowledge_dir=os.environ.get("AIVF_KNOWLEDGE_DIR", "/app/knowledge_base_v3"),
    output_dir=os.environ.get("AIVF_OUTPUT_DIR", "/app/output"),
    destination=source_root,
    include_output=True,
)
verified = verify_backup(created["path"])
if not verified["ok"]:
    raise SystemExit(f"backup verification failed: {verified}")
archive = Path(args.archive)
archive.parent.mkdir(parents=True, exist_ok=True)
shutil.make_archive(str(archive.with_suffix("")), "gztar", root_dir=created["path"])
print(archive)