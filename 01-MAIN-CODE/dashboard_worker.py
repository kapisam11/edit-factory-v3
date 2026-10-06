"""Spawn-safe worker launcher."""
import os
from pathlib import Path
import sys
import threading
import uuid

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WEB_DIR = _REPO_ROOT / "02-WEB-FILES"
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))


def _skip_stages_for_workflow(workflow: str) -> list[str]:
    """Translate the dashboard workflow name into the canonical pipeline stages."""
    from ai_video_factory.validation import normalize_workflow
    selected = normalize_workflow(workflow)
    if selected == "v3":
        return []
    configured = {
        "default": {"research", "plan", "script", "thumbnail", "auto_edit", "voiceover", "music", "quality_control", "metadata", "metrics"},
        "fast": {"plan", "script", "auto_edit", "metadata"},
        "package_only": {"research", "plan", "script", "thumbnail", "metadata"},
    }[selected]
    all_names = ["research", "plan", "script", "thumbnail", "auto_edit", "voiceover", "music", "quality_control", "metadata", "metrics"]
    return [name for name in all_names if name not in configured]


def run_job(job_id: str, params: dict, secrets: dict, output_root: str, db_path: str) -> None:
    """Spawn-safe worker entrypoint; all pipeline choices are explicit arguments."""
    os.environ["AIVF_WORKER_PROCESS"] = "1"
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
    from app import web_app_v3
    from ai_video_factory.validation import validate_target_seconds, validate_v3_target_seconds
    from dashboard_store import DashboardStore

    heartbeat_stop = threading.Event()
    heartbeat_store = DashboardStore(db_path)
    attempt_id = ""
    lease_token = ""
    try:
        raw_interval = os.environ.get("AIVF_WORKER_HEARTBEAT_SECONDS", "15")
        parsed_interval = float(raw_interval)
        if parsed_interval != parsed_interval or parsed_interval <= 0:
            raise ValueError
        interval = max(5.0, parsed_interval)
    except (TypeError, ValueError):
        try:
            heartbeat_store.update_job_if_status(
                job_id,
                ("queued", "running"),
                status="error",
                step="failed",
                error="AIVF_WORKER_HEARTBEAT_SECONDS must be numeric and greater than 0",
            )
        except Exception:
            pass
        return

    def heartbeat() -> None:
        failures = 0
        while not heartbeat_stop.wait(interval):
            try:
                alive = heartbeat_store.heartbeat_attempt(job_id, attempt_id, lease_token)
            except Exception:
                alive = False
            if alive:
                failures = 0
            else:
                failures += 1
                # A transient SQLite lock must not terminate the lease-refresh thread.
                if failures >= 5:
                    failures = 0

    worker_id = f"pid:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    attempt = heartbeat_store.begin_attempt(
        job_id,
        worker_id,
        str(Path(output_root).resolve() / ".work" / job_id),
    )
    if not attempt:
        return
    attempt_id = attempt["attempt_id"]
    lease_token = attempt["lease_token"]
    params = dict(params)
    params["_attempt_id"] = attempt_id
    params["_lease_token"] = lease_token
    params["_worker_id"] = worker_id
    params["_workspace_root"] = str(
        Path(output_root).resolve() / ".work" / job_id / attempt_id
    )

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        name=f"aivf-worker-heartbeat-{job_id}",
        daemon=True,
    )
    heartbeat_thread.start()
    params = dict(params)
    workflow = str(params.get("workflow", "default")).strip().lower()
    if workflow == "v3":
        params["target_seconds"] = validate_v3_target_seconds(params.get("target_seconds", 30.0))
    else:
        params["target_seconds"] = validate_target_seconds(params.get("target_seconds", 45.0))
    params["workflow"] = workflow
    skip_stages = _skip_stages_for_workflow(workflow)
    try:
        web_app_v3._run_job_worker_impl(
            job_id, params, secrets, output_root, db_path, skip_stages=skip_stages
        )
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=min(interval, 5.0))
        try:
            heartbeat_store.release_resources(job_id)
        except Exception:
            pass

        try:
            final = heartbeat_store.get_job(job_id) or {}
            status = str(final.get("status") or "unknown")
            heartbeat_store.finish_attempt(
                job_id,
                attempt_id,
                lease_token,
                status="succeeded" if status == "done" else (
                    "cancelled" if status in {"cancelled", "cancelling"} else "failed"
                ),
            )
        except Exception:
            pass
