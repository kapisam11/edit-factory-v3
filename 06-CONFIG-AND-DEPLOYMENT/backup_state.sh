#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${AIVF_STATE_DIR:-/app/state}"
KNOWLEDGE_DIR="${AIVF_KNOWLEDGE_DIR:-/app/knowledge_base_v3}"
OUTPUT_DIR="${AIVF_OUTPUT_DIR:-/app/output}"
BACKUP_DIR="${AIVF_BACKUP_DIR:-./backups}"
REMOTE="${AIVF_BACKUP_RCLONE_REMOTE:-}"

python -m ai_video_factory.janitor "$STATE_DIR" >/dev/null 2>&1 || true
mkdir -p "$BACKUP_DIR"

python - "$STATE_DIR" "$KNOWLEDGE_DIR" "$OUTPUT_DIR" "$BACKUP_DIR" <<'PY'
import sys
from ai_video_factory.backup_restore import create_backup, verify_backup

state, knowledge, output, destination = sys.argv[1:5]
result = create_backup(
    state_dir=state,
    knowledge_dir=knowledge,
    output_dir=output,
    destination=destination,
    include_output=True,
)
verification = verify_backup(result["path"])
if not verification["ok"]:
    raise SystemExit(f"backup verification failed: {verification}")
print(result["path"])
PY

if [[ -n "$REMOTE" ]]; then
  command -v rclone >/dev/null 2>&1 || {
    echo "AIVF_BACKUP_RCLONE_REMOTE is set but rclone is unavailable" >&2
    exit 1
  }
  latest="$(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name 'aivf-backup-*' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)"
  test -n "$latest"
  rclone copy "$latest" "$REMOTE/$(basename "$latest")" --immutable
fi
