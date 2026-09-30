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
import sys
import time
from pathlib import Path
import requests

sys.path.insert(0, "/app/01-MAIN-CODE")

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
metadata_path = package / "upload" / "youtube_shorts" / "metadata.json"
thumbnail_path = package / "thumbnail.png"
thumbnail_vertical_path = package / "thumbnail_vertical.png"
for required in (
    final_video,
    readiness_path,
    metadata_path,
    thumbnail_path,
    thumbnail_vertical_path,
    package / "source_media_metadata.json",
    package / "final_media_metadata.json",
    package / "resource_usage.json",
    package / "artifact_manifest.json",
    package / "release_evidence.json",
    package / "environment_fingerprint.json",
):
    if not required.is_file():
        raise SystemExit(f"target smoke required artifact is missing: {required}")

for metadata_file in (
    package / "source_media_metadata.json",
    package / "final_media_metadata.json",
):
    payload = json.loads(metadata_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("error") or not payload.get("streams"):
        raise SystemExit(f"target smoke metadata validation failed: {metadata_file}")

resource_usage = json.loads((package / "resource_usage.json").read_text(encoding="utf-8"))
if not isinstance(resource_usage, dict) or "elapsed_seconds" not in resource_usage:
    raise SystemExit("target smoke resource telemetry is malformed")

subprocess.run([
    "ffprobe", "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", str(final_video),
], check=True, stdout=subprocess.DEVNULL)
from ai_video_factory.production_assurance import verify_artifact_manifest

artifact_manifest = json.loads((package / "artifact_manifest.json").read_text(encoding="utf-8"))
integrity = verify_artifact_manifest(package, artifact_manifest)
if not integrity["ok"]:
    raise SystemExit(f"target smoke artifact integrity failed: {integrity['errors'][:5]}")

release_evidence = json.loads((package / "release_evidence.json").read_text(encoding="utf-8"))
if release_evidence.get("human_review_required") is not True:
    raise SystemExit("target smoke release evidence lost the human-review boundary")
if release_evidence.get("release_candidate") is not True:
    raise SystemExit("target smoke automated release evidence did not pass")

readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
checks = readiness.get("checks", {})
for key in ("MEDIA_VALID", "MEDIA_CONTRACT_VALID", "UPLOAD_PACKAGE_VALID"):
    if checks.get(key) is not True:
        raise SystemExit(f"target smoke readiness {key} failed")
if readiness.get("state") != "UPLOAD_PACKAGE_VALID":
    raise SystemExit(f"target smoke readiness state was {readiness.get('state')!r}")

metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
if metadata.get("metadata_quality", {}).get("passed") is not True:
    raise SystemExit("target smoke metadata quality gate failed")
if metadata.get("publish_ready") is not True:
    raise SystemExit("target smoke upload package is not publish-ready")
if metadata.get("media_rights", {}).get("publish_blocked") is True:
    raise SystemExit("target smoke media rights gate unexpectedly blocked synthetic fixture")

from PIL import Image
for image_path, expected in (
    (thumbnail_path, (1280, 720)),
    (thumbnail_vertical_path, (1080, 1920)),
):
    with Image.open(image_path) as image:
        if image.size != expected:
            raise SystemExit(f"target smoke thumbnail size mismatch for {image_path}: {image.size} != {expected}")

preview = session.get(f"{BASE}/api/jobs/{job_id}/preview", timeout=90)
if preview.status_code != 200 or not preview.content:
    raise SystemExit(f"target smoke preview failed: HTTP {preview.status_code}")

print("[AIVF] target-host V3 smoke passed")
print(json.dumps({"job_id": job_id, "package": package_name, "readiness_state": readiness.get("state")}, indent=2))

from dashboard_store import DashboardStore

state_dir = Path(os.environ.get("AIVF_STATE_DIR", "/app/state")).resolve()
db_path = state_dir / "jobs.db"
store = DashboardStore(db_path)

job_row = store.get_job(job_id)
if not job_row or job_row.get("status") != "done":
    raise SystemExit("target smoke could not find its completed job record")
log_rows = store.logs_since(job_id, 0)

quarantine = package.with_name(f".{package.name}.cleanup-{os.getpid()}")
if quarantine.exists():
    shutil.rmtree(quarantine, ignore_errors=True)

os.replace(package, quarantine)

try:
    deleted = store.write(
        lambda conn: (
            conn.execute("DELETE FROM job_logs WHERE job_id=?", (job_id,)),
            conn.execute("DELETE FROM job_events WHERE job_id=?", (job_id,)),
            conn.execute("DELETE FROM jobs WHERE id=? AND status='done'", (job_id,)),
        )
    )
    if not deleted[-1].rowcount:
        raise RuntimeError("target smoke could not remove its completed job record safely")

    cleanup_error = None
    for attempt in range(5):
        try:
            shutil.rmtree(quarantine, ignore_errors=False)
            cleanup_error = None
            break
        except OSError as exc:
            cleanup_error = exc
            time.sleep(0.2 * (attempt + 1))
    if cleanup_error is not None:
        raise cleanup_error
except Exception:
    if not package.exists() and quarantine.exists():
        os.replace(quarantine, package)

    def restore(conn):
        columns = list(job_row.keys())
        quoted = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT OR REPLACE INTO jobs ({quoted}) VALUES ({placeholders})",
            [job_row[column] for column in columns],
        )
        for log in log_rows:
            conn.execute(
                "INSERT OR REPLACE INTO job_logs (id, job_id, created_at, level, message) VALUES (?, ?, ?, ?, ?)",
                (log["id"], job_id, log["created_at"], log["level"], log["message"]),
            )

    store.write(restore)
    raise

fixture.unlink(missing_ok=True)
PY
