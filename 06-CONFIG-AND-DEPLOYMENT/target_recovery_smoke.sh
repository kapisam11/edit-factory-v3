#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${AIVF_COMPOSE_FILE:-06-CONFIG-AND-DEPLOYMENT/docker-compose.yml}"
SERVICE="${AIVF_COMPOSE_SERVICE:-web}"

echo "[AIVF] target-host recovery smoke: cancellation + restart reconciliation"
docker compose -f "$COMPOSE_FILE" config >/dev/null

create_job() {
  docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" python - <<'PY'
import os
import sys
import requests

sys.path.insert(0, "/app/01-MAIN-CODE")
BASE = "http://127.0.0.1:5000"
TOKEN = os.environ["AIVF_DASHBOARD_TOKEN"]

session = requests.Session()
login = session.post(
    f"{BASE}/login",
    data={"token": TOKEN},
    headers={"Origin": BASE},
    allow_redirects=False,
    timeout=15,
)
if login.status_code not in (302, 303):
    raise SystemExit(f"recovery smoke login failed: {login.status_code}")

for cookie in login.cookies:
    session.cookies.set(cookie.name, cookie.value, path=cookie.path or "/", secure=False)

fixture = "/tmp/aivf-recovery-smoke.mp4"
with open(fixture, "rb") as handle:
    job = session.post(
        f"{BASE}/api/jobs",
        data={
            "topic": "target host recovery smoke",
            "target_seconds": "120",
            "workflow": "v3",
            "platform": "tiktok",
            "audience": "recovery smoke viewers",
            "context": "recovery smoke",
            "bpm": "120",
            "enable_ocr": "false",
            "enable_object_detection": "false",
            "enable_diarization": "false",
        },
        files={"raw_video": ("recovery-smoke.mp4", handle, "video/mp4")},
        headers={"Origin": BASE},
        timeout=30,
    )
if job.status_code not in (200, 201, 202):
    raise SystemExit(f"job creation failed: {job.status_code} {job.text[:1000]}")
job_id = str(job.json().get("job_id") or "")
if not job_id:
    raise SystemExit("job creation returned no job_id")
print(job_id)
PY
}

wait_for_status() {
  local job_id="$1"
  local expected="$2"
  docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" env JOB_ID="$job_id" EXPECTED="$expected" python - <<'PY'
import os
import time
import requests

BASE = "http://127.0.0.1:5000"
TOKEN = os.environ["AIVF_DASHBOARD_TOKEN"]
JOB_ID = os.environ["JOB_ID"]
EXPECTED = os.environ["EXPECTED"]

session = requests.Session()
login = session.post(
    f"{BASE}/login",
    data={"token": TOKEN},
    headers={"Origin": BASE},
    allow_redirects=False,
    timeout=15,
)
if login.status_code not in (302, 303):
    raise SystemExit(f"status smoke login failed: {login.status_code}")
for cookie in login.cookies:
    session.cookies.set(cookie.name, cookie.value, path=cookie.path or "/", secure=False)

last = None
for _ in range(45):
    payload = session.get(f"{BASE}/api/jobs/{JOB_ID}/status", timeout=15).json()
    last = payload.get("status")
    if last == EXPECTED:
        print(last)
        raise SystemExit(0)
    if last in {"error", "done", "cancelled"} and last != EXPECTED:
        break
    time.sleep(1)
raise SystemExit(f"expected {EXPECTED}, last status was {last!r}")
PY
}

prepare_fixture() {
  docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" ffmpeg \
    -hide_banner -loglevel error -y \
    -f lavfi -i 'testsrc=size=1080x1920:rate=24' \
    -f lavfi -i 'anullsrc=r=48000:cl=mono' \
    -t 60 -shortest -pix_fmt yuv420p \
    -c:v libx264 -c:a aac /tmp/aivf-recovery-smoke.mp4
}

prepare_fixture

echo "[AIVF] cancellation path"
CANCEL_JOB_ID="$(create_job)"
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" env JOB_ID="$CANCEL_JOB_ID" python - <<'PY'
import os
import requests

BASE = "http://127.0.0.1:5000"
TOKEN = os.environ["AIVF_DASHBOARD_TOKEN"]
JOB_ID = os.environ["JOB_ID"]

session = requests.Session()
login = session.post(
    f"{BASE}/login",
    data={"token": TOKEN},
    headers={"Origin": BASE},
    allow_redirects=False,
    timeout=15,
)
if login.status_code not in (302, 303):
    raise SystemExit(f"cancel smoke login failed: {login.status_code}")
for cookie in login.cookies:
    session.cookies.set(cookie.name, cookie.value, path=cookie.path or "/", secure=False)

response = session.post(
    f"{BASE}/api/jobs/{JOB_ID}/cancel",
    headers={"Origin": BASE},
    timeout=15,
)
if response.status_code not in (200, 202, 409):
    raise SystemExit(f"cancel returned HTTP {response.status_code}: {response.text[:1000]}")
PY
wait_for_status "$CANCEL_JOB_ID" "cancelled"

echo "[AIVF] restart/reconciliation path"
RESTART_JOB_ID="$(create_job)"
sleep 1
docker compose -f "$COMPOSE_FILE" restart "$SERVICE"
for attempt in $(seq 1 30); do
  state="$(docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" env JOB_ID="$RESTART_JOB_ID" python - <<'PY'
import os
import sqlite3

db = "/app/state/jobs.db"
job_id = os.environ["JOB_ID"]
with sqlite3.connect(db) as conn:
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
print(row[0] if row else "missing")
PY
)"
  case "$state" in
    interrupted)
      echo "[AIVF] restart reconciliation passed"
      break
      ;;
    error|cancelled|done|missing)
      echo "[AIVF] unexpected restart-recovery state: $state"
      exit 1
      ;;
  esac
  if [ "$attempt" -eq 30 ]; then
    echo "[AIVF] restart reconciliation timed out; last state=$state"
    exit 1
  fi
  sleep 1
done

if ! docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" python - <<'PY'
from pathlib import Path

orphans = []
for proc in Path("/proc").glob("[0-9]*"):
    try:
        command = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except (OSError, UnicodeDecodeError):
        continue
    if "ffmpeg" in command.lower():
        orphans.append(command.strip())
if orphans:
    print("\n".join(orphans))
    raise SystemExit(1)
PY
then
  echo "[AIVF] orphan FFmpeg process detected after restart"
  exit 1
fi

echo "[AIVF] target-host recovery smoke passed"
