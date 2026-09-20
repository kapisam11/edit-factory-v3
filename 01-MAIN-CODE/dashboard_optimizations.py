"""Install production-safe storage, caching, and small dashboard UX extensions."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from flask import Response, has_request_context, jsonify, request, send_from_directory

from dashboard_cache import HybridCache
from dashboard_store import DashboardStore
from resource_governor import (
    MAX_JOB_STORAGE_BYTES,
    MAX_RENDER_WALLCLOCK_SECONDS,
    MAX_SSE_LIFETIME_SECONDS,
    ResourceLimitExceeded,
    acquire_sse,
    check_job_creation_limits,
    job_storage_ok,
    principal_for_request,
    release_sse,
    total_storage_bytes,
)


def install_dashboard_optimizations(app_module: Any) -> None:
    """Install the storage/cache boundary once on the canonical dashboard module."""
    extensions = getattr(app_module.app, "extensions", None)
    if extensions is not None and extensions.get("aivf_optimizations") is not None:
        return
    if extensions is None:
        extensions = {}
        app_module.app.extensions = extensions
    extensions["aivf_optimizations"] = True

    store = DashboardStore(app_module.DB_PATH)
    store.ensure_indexes()
    cache = HybridCache(os.environ.get("AIVF_REDIS_URL"))
    app_module.dashboard_store = store
    app_module.dashboard_cache = cache

    app_module.get_db = store.connect

    original_init_db = app_module.init_db
    original_db_insert_job = app_module.db_insert_job
    original_db_update_job = app_module.db_update_job
    original_db_append_log = app_module.db_append_log
    original_get_settings = app_module.get_settings
    original_set_setting = app_module.set_setting

    def init_db() -> None:
        original_init_db()
        store.ensure_indexes()

    def db_insert_job(job_id: str, topic: str, params: dict) -> None:
        params = dict(params)
        principal = principal_for_request(request) if has_request_context() else str(params.get("_principal", "internal"))
        try:
            check_job_creation_limits(store.connect, principal, (app_module.UPLOAD_FOLDER, app_module.OUTPUT_FOLDER))
        except ResourceLimitExceeded as exc:
            raise ValueError(str(exc)) from exc
        params["_principal"] = principal
        store.insert_job(job_id, topic, params)
        cache.delete("jobs:list")

    def db_update_job(job_id: str, **kwargs: Any) -> int:
        result = store.update_job(job_id, **kwargs)
        cache.delete("jobs:list")
        return result

    def db_get_job(job_id: str) -> dict | None:
        return store.get_job(job_id)

    def db_append_log(job_id: str, level: str, message: str) -> None:
        store.append_log(job_id, level, message)

    def db_logs_since(job_id: str, last_id: int = 0) -> list[dict]:
        return store.logs_since(job_id, last_id)

    def db_list_jobs() -> list[dict]:
        cached = cache.get_json("jobs:list")
        if cached is not None:
            return cached
        value = store.list_jobs(100)
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
        cache.delete("jobs:list")

    def check_rate_limit(client_ip: str, max_requests: int = 10, window_seconds: int = 60) -> bool:
        return store.check_rate_limit(client_ip, max_requests=max_requests, window_seconds=window_seconds)

    app_module.init_db = init_db
    app_module.db_insert_job = db_insert_job
    app_module.db_update_job = db_update_job
    app_module.db_get_job = db_get_job
    app_module.db_append_log = db_append_log
    app_module.db_logs_since = db_logs_since
    app_module.db_list_jobs = db_list_jobs
    app_module.get_settings = get_settings
    app_module.set_setting = set_setting
    app_module.check_rate_limit = check_rate_limit

    original_start_job = app_module._start_job
    resource_watchdogs: set[str] = set()

    def _watch_resource_budget(job_id: str, process: Any) -> None:
        started = __import__("time").monotonic()
        deadline = started + MAX_RENDER_WALLCLOCK_SECONDS
        while process.is_alive():
            if __import__("time").monotonic() >= deadline:
                try:
                    from dashboard_compat import _terminate_process_tree
                    _terminate_process_tree(process)
                except Exception:
                    try:
                        process.terminate()
                    except Exception:
                        pass
                current = app_module.db_get_job(job_id) or {}
                if current.get("status") in {"queued", "running", "cancelling"}:
                    app_module.db_update_job(
                        job_id,
                        status="error",
                        step="resource_limit",
                        error=f"Render wall-clock budget exceeded ({MAX_RENDER_WALLCLOCK_SECONDS}s)",
                    )
                    app_module.db_append_log(job_id, "ERROR", "Render wall-clock budget exceeded")
                return
            current = app_module.db_get_job(job_id) or {}
            package_dir = current.get("pkg_dir")
            if package_dir and not job_storage_ok(package_dir):
                try:
                    from dashboard_compat import _terminate_process_tree
                    _terminate_process_tree(process)
                except Exception:
                    try:
                        process.terminate()
                    except Exception:
                        pass
                current = app_module.db_get_job(job_id) or {}
                if current.get("status") in {"queued", "running", "cancelling"}:
                    app_module.db_update_job(
                        job_id,
                        status="error",
                        step="resource_limit",
                        error=f"Per-job storage quota exceeded ({MAX_JOB_STORAGE_BYTES // (1024 * 1024)} MiB)",
                    )
                    app_module.db_append_log(job_id, "ERROR", "Per-job storage quota exceeded")
                return
            __import__("time").sleep(0.5)

    def governed_start_job(job_id, params, secrets):
        started = original_start_job(job_id, params, secrets)
        process = app_module._active_processes.get(job_id)
        if started and process is not None:
            thread = __import__("threading").Thread(
                target=_watch_resource_budget,
                args=(job_id, process),
                name=f"aivf-budget-{job_id}",
                daemon=True,
            )
            thread.start()
            resource_watchdogs.add(job_id)
        return started

    app_module._start_job = governed_start_job

    original_log_stream = app_module.app.view_functions.get("job_logs_stream")
    if original_log_stream is not None:
        def bounded_log_stream(job_id: str):
            if not acquire_sse():
                return jsonify({"error": "SSE connection capacity reached"}), 429
            try:
                response = original_log_stream(job_id)
            except Exception:
                release_sse()
                raise
            if not isinstance(response, Response):
                release_sse()
                return response
            original_iter = response.response

            def bounded_iter():
                deadline = __import__("time").monotonic() + MAX_SSE_LIFETIME_SECONDS
                try:
                    for chunk in original_iter:
                        yield chunk
                        if __import__("time").monotonic() >= deadline:
                            yield "event: limit\\ndata: {\\"error\\":\\"SSE lifetime limit reached\\"}\\n\\n"
                            break
                finally:
                    release_sse()

            response.response = bounded_iter()
            response.headers["X-AIVF-SSE-Limit-Seconds"] = str(MAX_SSE_LIFETIME_SECONDS)
            return response

        app_module.app.view_functions["job_logs_stream"] = bounded_log_stream

    cleanup_state = {"last": 0.0}

    def _database_cleanup() -> None:
        import time
        now = time.monotonic()
        interval = max(300.0, float(os.environ.get("AIVF_DB_CLEANUP_INTERVAL_SECONDS", "3600")))
        if now - cleanup_state["last"] < interval:
            return
        cleanup_state["last"] = now
        days = max(1.0, float(os.environ.get("AIVF_DB_RETENTION_DAYS", os.environ.get("AIVF_RETENTION_DAYS", "30"))))
        cutoff = time.time() - days * 86400
        with store.connect() as conn:
            stale = conn.execute(
                "SELECT id, pkg_dir FROM jobs WHERE status IN ('done','error','cancelled','interrupted') AND updated_at < datetime(?, 'unixepoch')",
                (cutoff,),
            ).fetchall()
            stale_ids = [row["id"] for row in stale]
            if stale_ids:
                placeholders = ",".join("?" for _ in stale_ids)
                conn.execute(f"DELETE FROM job_logs WHERE job_id IN ({placeholders})", stale_ids)
                conn.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", stale_ids)
            conn.execute("DELETE FROM rate_limits WHERE ts < ?", (cutoff,))
        upload_cutoff = cutoff
        try:
            for child in app_module.UPLOAD_FOLDER.iterdir():
                try:
                    if child.is_file() and child.stat().st_mtime < upload_cutoff:
                        child.unlink(missing_ok=True)
                except OSError:
                    continue
        except OSError:
            pass

    @app_module.app.before_request
    def governed_resource_maintenance():
        _database_cleanup()

    @app_module.app.get("/api/jobs/<job_id>/preview")
    def job_preview(job_id: str):
        job = app_module.db_get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        if job.get("status") != "done":
            return jsonify({"error": "Preview is available after a job completes"}), 409

        package_value = str(job.get("pkg_dir") or "")
        package = Path(package_value).resolve()
        output_root = app_module.OUTPUT_FOLDER.resolve()
        if output_root not in package.parents or not package.is_dir():
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

        try:
            params = json.loads(job.get("params") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return jsonify({"error": "Job parameters are invalid"}), 409
        if not isinstance(params, dict):
            return jsonify({"error": "Job parameters are invalid"}), 409

        previous_status = job["status"]
        previous_step = job.get("step") or "failed"
        previous_error = job.get("error")
        previous_pkg_dir = job.get("pkg_dir")

        secrets = app_module.get_runtime_default_secrets()
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

        app_module._runtime_secrets[job_id] = secrets
        cache.delete("jobs:list")
        try:
            started = app_module._start_job(job_id, params, secrets)
        except Exception as exc:
            app_module._runtime_secrets.pop(job_id, None)
            store.update_job_if_status(
                job_id,
                ("queued",),
                status=previous_status,
                step=previous_step,
                error=previous_error,
                pkg_dir=previous_pkg_dir,
            )
            cache.delete("jobs:list")
            return jsonify({"error": f"Retry could not start: {exc}"}), 500

        final_job = app_module.db_get_job(job_id) or {}
        if final_job.get("status") == "error":
            app_module._runtime_secrets.pop(job_id, None)
            cache.delete("jobs:list")
            return jsonify({
                "error": final_job.get("error") or "Retry could not start",
                "job_id": job_id,
                "status": "error",
            }), 500

        return jsonify({"job_id": job_id, "status": "running" if started else "queued"}), 202
