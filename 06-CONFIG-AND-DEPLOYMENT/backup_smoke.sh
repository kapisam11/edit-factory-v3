#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${AIVF_COMPOSE_FILE:-06-CONFIG-AND-DEPLOYMENT/docker-compose.yml}"
SERVICE="${AIVF_COMPOSE_SERVICE:-web}"
REMOTE_ARCHIVE="/tmp/aivf-backup-$$.tar.gz"
LOCAL_ARCHIVE="${AIVF_BACKUP_ARCHIVE:-./aivf-backup.tar.gz}"

docker compose -f "$COMPOSE_FILE" config >/dev/null
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" python /app/06-CONFIG-AND-DEPLOYMENT/backup_smoke.py --archive "$REMOTE_ARCHIVE"
CONTAINER_ID="$(docker compose -f "$COMPOSE_FILE" ps -q "$SERVICE")"
test -n "$CONTAINER_ID"
docker cp "$CONTAINER_ID:$REMOTE_ARCHIVE" "$LOCAL_ARCHIVE"
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" rm -f "$REMOTE_ARCHIVE" || true
test -s "$LOCAL_ARCHIVE"

if [ "${AIVF_ENV:-production}" = "production" ] && [ -z "${AIVF_BACKUP_S3_URI:-}" ] && [ "${AIVF_BACKUP_ALLOW_LOCAL_ONLY:-0}" != "1" ]; then
  echo "[AIVF] production backup requires AIVF_BACKUP_S3_URI for off-host recovery"
  exit 1
fi

if [ -n "${AIVF_BACKUP_S3_URI:-}" ]; then
  command -v aws >/dev/null 2>&1 || { echo "[AIVF] aws CLI required for off-host upload"; exit 1; }
  aws s3 cp "$LOCAL_ARCHIVE" "${AIVF_BACKUP_S3_URI%/}/$(basename "$LOCAL_ARCHIVE")" --sse AES256
  echo "[AIVF] encrypted off-host backup uploaded"
else
  echo "[AIVF] verified local backup: $LOCAL_ARCHIVE"
fi