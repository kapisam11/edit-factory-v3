"""Spawn-safe worker entrypoint with isolated process-group semantics."""
import os

from ai_video_factory import render_engine
from ai_video_factory.ffmpeg_budget import wrap_run_ffmpeg


def run_job(job_id: str, params: dict, secrets: dict, output_root: str, db_path: str) -> None:
    os.environ["AIVF_WORKER_PROCESS"] = "1"
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
    render_engine.run_ffmpeg = wrap_run_ffmpeg(render_engine.run_ffmpeg)
    from app import web_app_v3
    web_app_v3._run_job_worker_impl(job_id, params, secrets, output_root, db_path)
