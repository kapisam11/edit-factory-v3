"""Spawn-safe worker launcher."""
import os
from pathlib import Path
import sys
import threading

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


def run_job(
    job_id: str,
    params: dict,
    secrets: dict,
    output_root: str,
    db_path: str,
    attempt_id: str | None = None,
    lease_token: str | None = None,
    worker_id: str | None = None,
    workspace_dir: str | None = None,
    publish_dir: str | None = None,
) -> None:
    """Spawn-safe worker entrypoint with optional fenced-attempt ownership."""
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
    try:
        raw_interval = os.environ.get("AIVF_WORKER_HEARTBEAT_SECONDS", "15")
        parsed_interval = float(raw_interval)
        if parsed_interval != parsed_interval or parsed_interval <= 0:
            raise ValueError
        interval = max(5.0, parsed_interval)
    except (TypeError, ValueError):
        try:
            if attempt_id and lease_token:
                heartbeat_store.update_job_if_owned(
                    job_id,
                    attempt_id,
                    lease_token,
                    status="error",
                    step="failed",
                    error="AIVF_WORKER_HEARTBEAT_SECONDS must be numeric and greater than 0",
                )
            else:
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
                alive = heartbeat_store.heartbeat_job(
                    job_id,
                    attempt_id=attempt_id,
                    lease_token=lease_token,
                )
            except Exception:
                alive = False
            if alive:
                failures = 0
                if worker_id:
                    try:
                        heartbeat_store.heartbeat_worker(worker_id)
                    except Exception:
                        pass
            else:
                failures += 1
                # A transient SQLite lock must not terminate the lease-refresh thread.
                if failures >= 5:
                    failures = 0

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
        worker_kwargs = {"skip_stages": skip_stages}
        if attempt_id is not None:
            worker_kwargs.update({
                "attempt_id": attempt_id,
                "lease_token": lease_token,
                "worker_id": worker_id,
                "workspace_dir": workspace_dir,
                "publish_dir": publish_dir,
            })
        web_app_v3._run_job_worker_impl(
            job_id,
            params,
            secrets,
            output_root,
            db_path,
            **worker_kwargs,
        )
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=min(interval, 5.0))
