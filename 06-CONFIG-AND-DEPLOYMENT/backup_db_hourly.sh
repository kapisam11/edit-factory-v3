#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${AIVF_STATE_DIR:-/app/state}"
BACKUP_DIR="${AIVF_DB_BACKUP_DIR:-$STATE_DIR/db-backups}"
REMOTE="${AIVF_DB_BACKUP_RCLONE_REMOTE:-}"
RETENTION_DAYS="${AIVF_DB_BACKUP_RETENTION_DAYS:-7}"

mkdir -p "$BACKUP_DIR"
python -m ai_video_factory.backup_restore db-backup   --db "$STATE_DIR/jobs.db"   --output "$BACKUP_DIR"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'jobs-*.db'   -mtime "+$((RETENTION_DAYS - 1))" -delete

latest="$(ls -1t "$BACKUP_DIR"/jobs-*.db 2>/dev/null | head -1 || true)"
test -n "$latest"
if [[ -n "$REMOTE" ]]; then
  command -v rclone >/dev/null 2>&1 || {
    echo "AIVF_DB_BACKUP_RCLONE_REMOTE is set but rclone is unavailable" >&2
    exit 1
  }
  rclone copy "$latest" "$REMOTE" --immutable
fi

echo "[AIVF] hourly database backup verified: $latest"
