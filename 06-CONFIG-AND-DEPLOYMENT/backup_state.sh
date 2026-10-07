#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${AIVF_STATE_DIR:-/app/state}"
KNOWLEDGE_DIR="${AIVF_KNOWLEDGE_DIR:-/app/knowledge_base_v3}"
OUTPUT_DIR="${AIVF_OUTPUT_DIR:-/app/output}"
BACKUP_DIR="${AIVF_BACKUP_DIR:-./backups}"
REMOTE="${AIVF_BACKUP_RCLONE_REMOTE:-}"
RETENTION_DAYS="${AIVF_BACKUP_RETENTION_DAYS:-30}"

mkdir -p "$BACKUP_DIR"
ARCHIVE="$BACKUP_DIR/aivf-full-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"

python -m ai_video_factory.backup_restore backup   --db "$STATE_DIR/jobs.db"   --knowledge "$KNOWLEDGE_DIR"   --output "$OUTPUT_DIR"   --backup "$ARCHIVE"   --staging "$STATE_DIR/.backup-staging"

python -m ai_video_factory.backup_restore verify   --backup "$ARCHIVE"   --marker "$STATE_DIR/backup-restore-verified"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'aivf-full-*.tar.gz'   -mtime "+$((RETENTION_DAYS - 1))" -delete

if [[ -n "$REMOTE" ]]; then
  command -v rclone >/dev/null 2>&1 || {
    echo "AIVF_BACKUP_RCLONE_REMOTE is set but rclone is unavailable" >&2
    exit 1
  }
  # Configure the remote as an rclone crypt: remote when encrypted off-host
  # storage is required. The backup command never writes credentials to logs.
  rclone copy "$ARCHIVE" "$REMOTE" --immutable
fi

echo "[AIVF] full backup verified: $ARCHIVE"
