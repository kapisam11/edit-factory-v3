#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${AIVF_COMPOSE_FILE:-06-CONFIG-AND-DEPLOYMENT/docker-compose.yml}"
SERVICE="${AIVF_COMPOSE_SERVICE:-web}"

echo "[AIVF] target-host V3 smoke: ${SERVICE} (${COMPOSE_FILE})"
docker compose -f "$COMPOSE_FILE" config >/dev/null

docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" python - <<'PY'
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
import requests

BASE = "http://127.0.0.1:5000"
TOKEN = os.environ.get("AIVF_DASHBOARD_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("AIVF_DASHBOARD_TOKEN is not available inside the web container")

fixture = Path("/tmp/aivf-release-smoke.mp4")
subprocess.run([
    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=24",
    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
    "-t", "10", "-shortest", "-pix_fmt", "yuv420p",
    "-c:v", "libx264", "-c:a", "aac", str(fixture),
], check=True)

session = requests.Session()
login = session.post(
    f"{BASE}/login",
    data={"token": TOKEN},
    headers={"Origin": BASE},
    allow_redirects=False,
    timeout=15,
)
if login.status_code not in (302, 303):
    raise SystemExit(f"target smoke login failed: HTTP {login.status_code}")
for cookie in login.cookies:
    session.cookies.set(cookie.name, cookie.value, path=cookie.path or "/", secure=False)

with fixture.open("rb") as handle:
    create = session.post(
        f"{BASE}/api/jobs",
        data={
            "topic": "target host V3 smoke",
            "target_seconds": "8",
            "workflow": "v3",
            "platform": "youtube_shorts",
            "audience": "release smoke viewers",
            "context": "deterministic production smoke",
            "bpm": "120",
            "enable_ocr": "false",
            "enable_object_detection": "false",
            "enable_diarization": "false",
        },
        files={"raw_video": (fixture.name, handle, "video/mp4")},
        headers={"Origin": BASE},
        timeout=30,
    )

if create.status_code not in (200, 201, 202):
    raise SystemExit(f"target smoke job creation failed: HTTP {create.status_code}: {create.text[:1000]}")
job_id = str(create.json().get("job_id") or "")
if not job_id:
    raise SystemExit("target smoke did not return job_id")

for _ in range(180):
    status = session.get(f"{BASE}/api/jobs/{job_id}/status", timeout=15).json()
    state = status.get("status")
    if state == "done":
        break
    if state in {"error", "interrupted", "cancelled"}:
        detail = session.get(f"{BASE}/api/jobs/{job_id}", timeout=15).text
        raise SystemExit(f"target smoke job failed: {detail[:3000]}")
    time.sleep(2)
else:
    raise SystemExit("target smoke timed out waiting for V3 job")

job_detail = session.get(f"{BASE}/api/jobs/{job_id}", timeout=15).json()
package_name = str(job_detail.get("package_name") or "")
if not package_name:
    raise SystemExit("target smoke completed without package_name")

output_root = Path(os.environ.get("AIVF_OUTPUT_DIR", "/app/output")).resolve()
package = (output_root / package_name).resolve()
if output_root not in package.parents or not package.is_dir():
    raise SystemExit("target smoke package path escaped output root")

final_video = package / "final.v3.mp4"
readiness_path = package / "v3_readiness.json"
if not final_video.is_file() or not readiness_path.is_file():
    raise SystemExit("target smoke final V3 artifact or readiness report is missing")

subprocess.run([
    "ffprobe", "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", str(final_video),
], check=True, stdout=subprocess.DEVNULL)
readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
checks = readiness.get("checks", {})
for key in ("MEDIA_VALID", "MEDIA_CONTRACT_VALID", "UPLOAD_PACKAGE_VALID"):
    if checks.get(key) is not True:
        raise SystemExit(f"target smoke readiness {key} failed")
if readiness.get("state") != "UPLOAD_PACKAGE_VALID":
    raise SystemExit(f"target smoke readiness state was {readiness.get('state')!r}")

preview = session.get(f"{BASE}/api/jobs/{job_id}/preview", timeout=90)
if preview.status_code != 200 or not preview.content:
    raise SystemExit(f"target smoke preview failed: HTTP {preview.status_code}")

print("[AIVF] target-host V3 smoke passed")
print(json.dumps({"job_id": job_id, "package": package_name, "readiness_state": readiness.get("state")}, indent=2))
shutil.rmtree(package, ignore_errors=True)
fixture.unlink(missing_ok=True)
PY
