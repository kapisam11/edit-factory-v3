"""Spawn-safe worker entrypoint with isolated process-group semantics."""
import os


def run_job(job_id: str, params: dict, secrets: dict, output_root: str, db_path: str) -> None:
    os.environ["AIVF_WORKER_PROCESS"] = "1"
    if os.name != "nt":
        try:
            os.setsid()
        except OSError:
            pass
    from app import web_app_v2
    web_app_v2._run_job_worker_impl(job_id, params, secrets, output_root, db_path)
