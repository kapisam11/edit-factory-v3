#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${AIVF_COMPOSE_FILE:-06-CONFIG-AND-DEPLOYMENT/docker-compose.yml}"
SERVICE="${AIVF_COMPOSE_SERVICE:-web}"
ARCHIVE="${1:?usage: restore_smoke.sh <backup.tar.gz>}"
CONTAINER_ID="$(docker compose -f "$COMPOSE_FILE" ps -q "$SERVICE")"
test -n "$CONTAINER_ID"
REMOTE="/app/state/.restore-smoke-$.tar.gz"
docker cp "$ARCHIVE" "$CONTAINER_ID:$REMOTE"
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" python /app/06-CONFIG-AND-DEPLOYMENT/restore_smoke.py --archive "$REMOTE"
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" rm -f "$REMOTE" || true
echo "[AIVF] restore drill passed"