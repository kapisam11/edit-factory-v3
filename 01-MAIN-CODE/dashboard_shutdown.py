"""Gunicorn/process shutdown hook for active AIVF jobs."""
from importlib import import_module


def shutdown_active_workers() -> None:
    """Terminate all active dashboard workers and reconcile their job state."""
    web_app_v2 = import_module("app.web_app_v2")
    dashboard_compat = import_module("dashboard_compat")

    for job_id, process in list(web_app_v2._active_processes.items()):
        try:
            dashboard_compat._terminate_process_tree(process)
        finally:
            web_app_v2._active_processes.pop(job_id, None)
            web_app_v2._runtime_secrets.pop(job_id, None)
            job = web_app_v2.db_get_job(job_id)
            if job and job["status"] not in web_app_v2.TERMINAL_STATUSES:
                web_app_v2.db_update_job(
                    job_id,
                    status="interrupted",
                    step="interrupted",
                    error="Dashboard worker shut down",
                )
                web_app_v2.db_append_log(
                    job_id,
                    "ERROR",
                    "Dashboard worker shut down; job interrupted",
                )
