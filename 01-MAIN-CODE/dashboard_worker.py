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
    configured = {
        "default": {
            "research", "plan", "script", "thumbnail", "auto_edit",
            "voiceover", "music", "quality_control", "metadata", "metrics",
        },
        "fast": {"plan", "script", "auto_edit", "metadata"},
        "package_only": {"research", "plan", "script", "thumbnail", "metadata"},
    }[selected]
    all_names = [
        "research", "plan", "script", "thumbnail", "auto_edit",
        "voiceover", "music", "quality_control", "metadata", "metrics",
    ]
    return [name for name in all_names if name not in configured]


def run_job(job_id: str, params: dict, secrets: dict, output_root: str, db_path: str) -> None:
    os.environ["AIVF_WORKER_PROCESS"] = "1"
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
    from app import web_app_v2
    from ai_video_factory.validation import validate_target_seconds

    params = dict(params)
    params["target_seconds"] = validate_target_seconds(params.get("target_seconds", 45.0))
    params["workflow"] = str(params.get("workflow", "default"))
    skip_stages = _skip_stages_for_workflow(params["workflow"])

    if not skip_stages:
        web_app_v2._run_job_worker_impl(job_id, params, secrets, output_root, db_path)
        return

    # `_run_job_worker_impl` is the established worker contract. Keep that
    # contract stable while selecting the requested pipeline inside this spawned
    # process; no shared web-process state is modified.
    import ai_video_factory.pipeline as pipeline_module

    original_builder = pipeline_module.build_director_pipeline
    pipeline_module.build_director_pipeline = lambda: original_builder(skip_stages=skip_stages)
    try:
        web_app_v2._run_job_worker_impl(job_id, params, secrets, output_root, db_path)
    finally:
        pipeline_module.build_director_pipeline = original_builder
