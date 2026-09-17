"""Install production-safe storage, caching, and small dashboard UX extensions."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from flask import jsonify, request, send_from_directory

from dashboard_cache import HybridCache
from dashboard_store import DashboardStore


TERMINAL_STATUSES = {"done", "error", "cancelled", "interrupted"}


def install_dashboard_optimizations(app_module: Any) -> None:
    """Install the storage/cache boundary once on the canonical dashboard module."""
    if getattr(app_module, "_AIVF_OPTIMIZATIONS_INSTALLED", False):
        return
    app_module._AIVF_OPTIMIZATIONS_INSTALLED = True

    store = DashboardStore(app_module.DB_PATH)
    store.ensure_indexes()
    cache = HybridCache(os.environ.get("AIVF_REDIS_URL"))
    app_module.dashboard_store = store
    app_module.dashboard_cache = cache

    # Keep the existing public helper API intact while moving connections to a
    # reusable storage boundary. This also covers compatibility routes that call
    # web_app_v3.get_db() directly.
    app_module.get_db = store.connect

    original_init_db = app_module.init_db
    original_db_insert_job = app_module.db_insert_job
    original_db_update_job = app_module.db_update_job
    original_db_append_log = app_module.db_append_log
    original_db_list_jobs = app_module.db_list_jobs
    original_get_settings = app_module.get_settings
    original_set_setting = app_module.set_setting

    def init_db() -> None:
        original_init_db()
        store.ensure_indexes()

    def db_insert_job(job_id: str, topic: str, params: dict) -> None:
        store.insert_job(job_id, topic, params)
        cache.delete("jobs:list")

    def db_update_job(job_id: str, **kwargs: Any) -> int:
        result = store.update_job(job_id, **kwargs)
        cache.delete("jobs:list")
        return result

    def db_append_log(job_id: str, level: str, message: str) -> None:
        store.append_log(job_id, level, message)
        # Job detail includes logs, so invalidate the short-lived job listing.
        cache.delete("jobs:list")

    def db_list_jobs() -> list[dict]:
        cached = cache.get_json("jobs:list")
        if cached is not None:
            return cached
        value = original_db_list_jobs()
        cache.set_json("jobs:list", value, 1.0)
        return value

    def get_settings() -> dict:
        cached = cache.get_json("settings")
        if cached is not None:
            return cached
        value = original_get_settings()
        cache.set_json("settings", value, 5.0)
        return value

    def set_setting(key: str, value: Any) -> None:
        original_set_setting(key, value)
        cache.delete("settings")

    app_module.init_db = init_db
    app_module.db_insert_job = db_insert_job
    app_module.db_update_job = db_update_job
    app_module.db_append_log = db_append_log
    app_module.db_list_jobs = db_list_jobs
    app_module.get_settings = get_settings
    app_module.set_setting = set_setting

    @app_module.app.get("/api/jobs/<job_id>/preview")
    def job_preview(job_id: str):
        job = app_module.db_get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        if job.get("status") != "done":
            return jsonify({"error": "Preview is available after a job completes"}), 409
        package = app_module._resolve_package(Path(job.get("pkg_dir") or "").name)
        if not package:
            return jsonify({"error": "Package not found"}), 404
        preferred = ("final_with_music.mp4", "final_short.mp4", "final_short_vo.mp4")
        filename = next((name for name in preferred if (package / name).is_file()), None)
        if not filename:
            return jsonify({"error": "Rendered preview not found"}), 404
        response = send_from_directory(package, filename, mimetype="video/mp4")
        response.headers["Cache-Control"] = "private, max-age=60"
        return response

    @app_module.app.post("/api/jobs/<job_id>/retry")
    def retry_job(job_id: str):
        job = app_module.db_get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        if job.get("status") not in {"error", "interrupted"}:
            return jsonify({"error": "Only failed or interrupted jobs can be retried"}), 409
        changed = store.update_job_if_status(
            job_id,
            ("error", "interrupted"),
            status="queued",
            step="waiting",
            error=None,
            pkg_dir=None,
        )
        if not changed:
            return jsonify({"error": "Job changed before retry could start"}), 409
        secrets = app_module.get_runtime_default_secrets()
        app_module._runtime_secrets[job_id] = secrets
        cache.delete("jobs:list")
        started = app_module._start_job(job_id, __import__("json").loads(job.get("params") or "{}"), secrets)
        return jsonify({"job_id": job_id, "status": "running" if started else "queued"}), 202
