"""Spawn-safe worker launcher."""
import os
from pathlib import Path
import sys

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
    params = dict(params)
    workflow = str(params.get("workflow", "default")).strip().lower()
    if workflow == "v3":
        params["target_seconds"] = validate_v3_target_seconds(params.get("target_seconds", 30.0))
    else:
        params["target_seconds"] = validate_target_seconds(params.get("target_seconds", 45.0))
    params["workflow"] = workflow
    skip_stages = _skip_stages_for_workflow(workflow)
    web_app_v3._run_job_worker_impl(
        job_id, params, secrets, output_root, db_path, skip_stages=skip_stages
    )
