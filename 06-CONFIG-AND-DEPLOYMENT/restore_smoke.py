#!/usr/bin/env python3
from pathlib import Path
import argparse
import os
import shutil
import sys
import tarfile

parser = argparse.ArgumentParser()
parser.add_argument("--archive", required=True)
args = parser.parse_args()

archive = Path(args.archive)
root = Path(os.environ.get("AIVF_RESTORE_STAGING_DIR", "/app/state/.restore-smoke"))
shutil.rmtree(root, ignore_errors=True)
root.mkdir(parents=True)
with tarfile.open(archive, "r:gz") as handle:
    members = handle.getmembers()
    root_resolved = root.resolve()
    for member in members:
        target = (root / member.name).resolve()
        if target != root_resolved and root_resolved not in target.parents:
            raise SystemExit(f"unsafe archive member: {member.name}")
    handle.extractall(root, members=members)

manifests = list(root.glob("**/backup_manifest.json"))
if not manifests:
    raise SystemExit("restore smoke could not find backup manifest")
backup_dir = manifests[0].parent
sys.path.insert(0, "/app/01-MAIN-CODE")
from ai_video_factory.backup_restore import restore_backup, verify_backup

source = verify_backup(backup_dir)
if not source["ok"]:
    raise SystemExit(f"restore smoke source verification failed: {source}")

result = restore_backup(
    backup_dir=backup_dir,
    state_dir=root / "restored-state",
    knowledge_dir=root / "restored-knowledge",
    output_dir=root / "restored-output",
)
if not result["ok"]:
    raise SystemExit(f"restore failed: {result}")
if not (root / "restored-state" / "jobs.db").is_file():
    raise SystemExit("restored database is missing")
print("[AIVF] restore smoke passed")