#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:?usage: deploy_with_rollback.sh <registry/image@sha256:digest>}"
COMPOSE_FILE="${COMPOSE_FILE:-06-CONFIG-AND-DEPLOYMENT/docker-compose.yml}"
BACKUP_DIR="${AIVF_BACKUP_DIR:-/app/state/backups}"
PREVIOUS_FILE=".aivf-previous-image"
CURRENT_FILE=".aivf-current-image"
OFFHOST_MARKER="${AIVF_BACKUP_OFFHOST_MARKER:-${BACKUP_DIR}/offhost-verified}"
BACKUP_ID="${AIVF_DEPLOYMENT_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
BACKUP_ARCHIVE="${BACKUP_DIR}/predeploy-${BACKUP_ID}.tar.gz"
ROLLBACK_FAILED=0

printf '%s\n' "$IMAGE" | grep -Eq '^.+@sha256:[0-9a-fA-F]{64}$' || {
  echo "deployment image must be immutable: $IMAGE" >&2
  exit 2
}

previous=""
cid="$(docker ps --filter label=com.docker.compose.service=web -q | head -n 1 || true)"
if [ -n "$cid" ]; then
  previous="$(docker inspect -f '{{.Config.Image}}' "$cid" || true)"
fi
printf '%s\n' "$previous" | grep -Eq '^.+@sha256:[0-9a-fA-F]{64}$' || {
  echo "No pinned running image was found; refusing an unsafe production deployment." >&2
  exit 3
}
printf '%s\n' "$previous" > "$PREVIOUS_FILE"

rollback() {
  set +e
  echo "Deployment failed; rolling back to $previous" >&2
  docker pull "$previous"
  AIVF_IMAGE="$previous" AIVF_COOKIE_SECURE=1 docker compose -f "$COMPOSE_FILE" up -d --no-build --remove-orphans
  printf "%s\n" "$previous" > "$CURRENT_FILE"
  for _ in $(seq 1 10); do
    if curl -fsS http://127.0.0.1:5000/api/health >/dev/null; then
      return 0
    fi
    sleep 3
  done
  ROLLBACK_FAILED=1
  return 1
}

on_exit() {
  status=$?
  if [ "$status" -ne 0 ]; then
    rollback || true
  fi
  if [ "$ROLLBACK_FAILED" -ne 0 ]; then
    echo "ROLLBACK_FAILED" >&2
  fi
  exit "$status"
}
trap on_exit EXIT

mkdir -p "$BACKUP_DIR"
# The previous production image predates the new backup CLI. Run the backup
# tooling from the candidate image before changing the running service.
docker pull "$IMAGE"
AIVF_IMAGE="$IMAGE" docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T web aivf-backup backup   --db /app/state/jobs.db   --backup "$BACKUP_ARCHIVE"   --knowledge /app/knowledge_base_v3   --output /app/output
AIVF_IMAGE="$IMAGE" docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T web aivf-backup verify   --backup "$BACKUP_ARCHIVE"   --marker /app/state/backup-restore-verified
AIVF_IMAGE="$IMAGE" docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T web aivf-backup restore  --backup "$BACKUP_ARCHIVE" --destination /tmp/aivf-restore-drill

if [ -n "${AIVF_BACKUP_REMOTE:-}" ] || [ -n "${AIVF_BACKUP_REMOTE_DIR:-}" ]; then
  test -n "${AIVF_BACKUP_REMOTE:-}" || { echo "AIVF_BACKUP_REMOTE must be set with AIVF_BACKUP_REMOTE_DIR" >&2; exit 4; }
  test -n "${AIVF_BACKUP_REMOTE_DIR:-}" || { echo "AIVF_BACKUP_REMOTE_DIR must be set with AIVF_BACKUP_REMOTE" >&2; exit 4; }
  test -n "${AIVF_BACKUP_ENCRYPTION_KEY:-}" || { echo "AIVF_BACKUP_ENCRYPTION_KEY is required for off-host backup" >&2; exit 4; }
  bash "$(dirname "$0")/backup_offhost.sh" "$BACKUP_ARCHIVE" "$AIVF_BACKUP_REMOTE" "$AIVF_BACKUP_REMOTE_DIR"
  printf "%s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OFFHOST_MARKER"
elif [ "${AIVF_REQUIRE_OFFHOST_BACKUP:-0}" = "1" ]; then
  echo "Off-host backup is required but AIVF_BACKUP_REMOTE/AIVF_BACKUP_REMOTE_DIR are not configured." >&2
  exit 4
fi

AIVF_IMAGE="$IMAGE" docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T web aivf-db-migrate /app/state/jobs.db
AIVF_IMAGE="$IMAGE" AIVF_COOKIE_SECURE=1 docker compose -f "$COMPOSE_FILE" up -d --no-build --remove-orphans

for _ in $(seq 1 15); do
  if curl -fsS http://127.0.0.1:5000/api/health >/dev/null; then
    break
  fi
  sleep 3
done
curl -fsS http://127.0.0.1:5000/api/health >/dev/null

if [ -x 06-CONFIG-AND-DEPLOYMENT/target_v3_smoke.sh ]; then
  bash 06-CONFIG-AND-DEPLOYMENT/target_v3_smoke.sh
fi

printf "%s\n" "$IMAGE" > "$CURRENT_FILE"
trap - EXIT
echo "deployment succeeded: $IMAGE"
