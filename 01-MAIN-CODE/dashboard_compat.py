"""Compatibility routes plus queue, cancellation, retention, and secret management."""
import json
import os
import signal
import shutil
import subprocess
import threading
import time

from flask import jsonify, request, send_from_directory

SECRET_KEYS = {"groq_key", "model_key", "elevenlabs_key"}
_DASHBOARD_SECRETS = {key: "" for key in SECRET_KEYS}
_START_LOCK = threading.Lock()
_LIFECYCLE_LOCK = threading.RLock()
_LAST_CLEANUP = 0.0


def _terminate_process_tree(process):
    if not process.is_alive():
        process.join(timeout=1)
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            process.terminate()
    process.join(timeout=15)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)


def cancel_process(job_id):
    import web_app_v2

    # Atomically claim cancellation so a completion racing with this request
    # cannot be changed from a terminal state to `cancelled`.
    with web_app_v2.get_db() as conn:
        cursor = conn.execute(
            "UPDATE jobs SET status='cancelling', step='cancelling', updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status NOT IN ('done','error','cancelled','interrupted','cancelling')",
            (job_id,),
        )
        if cursor.rowcount == 0:
            row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                return jsonify({"error": "Job not found"}), 404
            return jsonify({"job_id": job_id, "status": row["status"]}), 409

    process = web_app_v2._active_processes.get(job_id)
    if process:
        _terminate_process_tree(process)
        if process.is_alive():
            return jsonify({"error": "Worker termination timed out"}), 503

    web_app_v2._active_processes.pop(job_id, None)
    web_app_v2._runtime_secrets.pop(job_id, None)
    web_app_v2.db_update_job(job_id, status="cancelled", step="cancelled")
    web_app_v2.db_append_log(job_id, "INFO", "Job cancelled")
    return jsonify({"job_id": job_id, "status": "cancelled"})


def _cleanup_old_packages(web_app_v2, max_age_days):
    try:
        max_age_days = max(0.0, float(max_age_days))
    except (TypeError, ValueError):
        return []

    cutoff = time.time() - max_age_days * 86400
    with web_app_v2.get_db() as conn:
        protected = {
            str(row["pkg_dir"])
            for row in conn.execute(
                "SELECT pkg_dir FROM jobs "
                "WHERE status IN ('queued','running','cancelling') AND pkg_dir IS NOT NULL"
            ).fetchall()
        }

    removed = []
    root = web_app_v2.OUTPUT_FOLDER.resolve()
    for child in root.iterdir():
        if not child.is_dir() or str(child.resolve()) in protected:
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child.name)
        except OSError:
            continue
    return removed


def _reconcile_worker_exit(web_app_v2, job_id, process):
    """Reconcile DB state before releasing the process bookkeeping entry."""
    try:
        exit_code = process.exitcode
        with web_app_v2.get_db() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status='interrupted', step='interrupted', "
                "error=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status IN ('queued','running')",
                (f"Worker exited unexpectedly with code {exit_code}", job_id),
            )
            cancelled_cursor = conn.execute(
                "UPDATE jobs SET status='cancelled', step='cancelled', updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status='cancelling'",
                (job_id,),
            )
        if cursor.rowcount or cancelled_cursor.rowcount:
            level = "INFO" if cancelled_cursor.rowcount else "ERROR"
            message = "Worker exited after cancellation" if cancelled_cursor.rowcount else "Worker exited unexpectedly"
            web_app_v2.db_append_log(job_id, level, message)
    except Exception:
        # Lifecycle cleanup must never leave an entry permanently retained just
        # because the diagnostic state update itself failed.
        web_app_v2.logger.exception("Could not reconcile worker exit for %s", job_id)


def _watch_job_process(web_app_v2, job_id, process):
    """Wait for a worker and reconcile unexpected termination before cleanup."""
    process.join()
    with _LIFECYCLE_LOCK:
        _reconcile_worker_exit(web_app_v2, job_id, process)
        web_app_v2._active_processes.pop(job_id, None)
        web_app_v2._runtime_secrets.pop(job_id, None)
        invalidate = getattr(web_app_v2, "_invalidate_package_cache", None)
        if callable(invalidate):
            invalidate()


def _reap_and_dispatch(web_app_v2):
    # Gunicorn uses gthread so lifecycle maintenance can run concurrently with
    # API requests. Serialize this stateful operation to protect process maps,
    # queue dispatch, and the cleanup timer.
    with _LIFECYCLE_LOCK:
        for job_id, process in list(web_app_v2._active_processes.items()):
            if process.is_alive():
                continue
            process.join(timeout=0)
            _reconcile_worker_exit(web_app_v2, job_id, process)
            web_app_v2._active_processes.pop(job_id, None)
            web_app_v2._runtime_secrets.pop(job_id, None)

        capacity = max(1, int(web_app_v2.get_settings()["max_concurrent_jobs"]))
        while sum(1 for p in web_app_v2._active_processes.values() if p.is_alive()) < capacity:
            with web_app_v2.get_db() as conn:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
            if not row:
                return
            job_id = row["id"]
            params = json.loads(row["params"] or "{}")
            secrets = dict(web_app_v2._runtime_secrets.get(job_id, _DASHBOARD_SECRETS))
            if not web_app_v2._start_job(job_id, params, secrets):
                return
            with web_app_v2.get_db() as conn:
                status = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            if status == "queued":
                return


def register_dashboard_compat(app):
    import web_app_v2

    with web_app_v2.get_db() as conn:
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS prevent_post_cancel_finalization
            BEFORE UPDATE OF status ON jobs
            WHEN OLD.status IN ('cancelling','cancelled')
                 AND NEW.status IN ('running','done','error','queued')
            BEGIN
                SELECT RAISE(ABORT, 'job cancellation already requested');
            END
        """)

    # PR #23 installs a watcher in web_app_v2._start_job. Replace it with the
    # lifecycle-aware watcher before retaining the start function so unexpected
    # worker exits cannot disappear from bookkeeping while the DB stays running.
    web_app_v2._watch_job_process = lambda job_id, process: _watch_job_process(
        web_app_v2, job_id, process
    )

    original_start_job = web_app_v2._start_job

    def locked_start_job(job_id, params, secrets):
        with _START_LOCK:
            with _LIFECYCLE_LOCK:
                try:
                    return original_start_job(job_id, params, secrets)
                except Exception as exc:
                    web_app_v2._active_processes.pop(job_id, None)
                    web_app_v2._runtime_secrets.pop(job_id, None)
                    try:
                        with web_app_v2.get_db() as conn:
                            cursor = conn.execute(
                                "UPDATE jobs SET status='error', step='failed', error=?, "
                                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",
                                (f"Worker failed to start: {exc}", job_id),
                            )
                        if cursor.rowcount:
                            web_app_v2.db_append_log(job_id, "ERROR", f"Worker failed to start: {exc}")
                    except Exception:
                        web_app_v2.logger.exception("Could not record worker-start failure for %s", job_id)
                    return False

    web_app_v2._start_job = locked_start_job

    def hardened_settings():
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            for key in SECRET_KEYS:
                if key in data:
                    value = str(data.get(key) or "").strip()
                    _DASHBOARD_SECRETS[key] = value
                    web_app_v2.set_runtime_default_secret(key, value)
            for key, value in data.items():
                if key not in SECRET_KEYS:
                    web_app_v2.set_setting(key, value)

        result = dict(web_app_v2.get_settings())
        result.update({f"{key}_configured": bool(value) for key, value in _DASHBOARD_SECRETS.items()})
        for key in SECRET_KEYS:
            result[key] = ""
        return jsonify(result)

    app.view_functions["settings"] = hardened_settings

    @app.route("/api/run", methods=["POST"])
    def compat_run():
        return app.view_functions["create_job"]()

    @app.route("/api/queue/status")
    def compat_queue_status():
        _reap_and_dispatch(web_app_v2)
        with web_app_v2.get_db() as conn:
            queued = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]
            running = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
        return jsonify({
            "queued": queued,
            "running": running,
            "max_concurrent_jobs": int(web_app_v2.get_settings()["max_concurrent_jobs"]),
        })

    @app.route("/api/presets", methods=["GET", "POST", "DELETE"])
    def compat_presets():
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            name = str(data.get("name", "")).strip()
            config = data.get("config") or {}
            if not name or len(name) > 80 or not isinstance(config, dict):
                return jsonify({"error": "Invalid preset"}), 400
            with web_app_v2.get_db() as conn:
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (f"preset:{name}", json.dumps(config)),
                )
            return jsonify({"ok": True}), 201
        with web_app_v2.get_db() as conn:
            rows = conn.execute(
                "SELECT key,value FROM settings WHERE key LIKE 'preset:%' ORDER BY key"
            ).fetchall()
        return jsonify({row["key"][7:]: json.loads(row["value"]) for row in rows})

    @app.route("/api/package/<name>/script", methods=["GET", "POST"])
    def compat_script(name):
        package = web_app_v2._resolve_package(name)
        if not package:
            return jsonify({"error": "Package not found"}), 404
        path = package / "script.txt"
        if request.method == "GET":
            return jsonify({"script": path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""})
        data = request.get_json(silent=True) or {}
        script = data.get("script")
        if not isinstance(script, str) or len(script) > 200000:
            return jsonify({"error": "Invalid script"}), 400
        path.write_text(script, encoding="utf-8")
        return jsonify({"ok": True})

    @app.route("/api/package/<name>/files")
    def compat_files(name):
        package = web_app_v2._resolve_package(name)
        if not package:
            return jsonify({"error": "Package not found"}), 404
        return jsonify([
            {"path": path.relative_to(package).as_posix(), "size": path.stat().st_size}
            for path in package.rglob("*") if path.is_file()
        ])

    @app.route("/api/package/<name>/file/<path:filename>")
    def compat_file(name, filename):
        package = web_app_v2._resolve_package(name)
        if not package:
            return jsonify({"error": "Package not found"}), 404
        return send_from_directory(package, filename)

    @app.route("/api/admin/cleanup", methods=["POST"])
    def cleanup_packages_admin():
        days = request.args.get("max_age_days", os.environ.get("AIVF_RETENTION_DAYS", "7"))
        removed = _cleanup_old_packages(web_app_v2, days)
        return jsonify({"removed": removed, "count": len(removed)})

    @app.before_request
    def lifecycle_maintenance():
        global _LAST_CLEANUP
        _reap_and_dispatch(web_app_v2)
        if os.environ.get("AIVF_DISABLE_AUTO_CLEANUP", "0") != "1":
            with _LIFECYCLE_LOCK:
                now = time.monotonic()
                interval = max(60.0, float(os.environ.get("AIVF_CLEANUP_INTERVAL_SECONDS", "21600")))
                if now - _LAST_CLEANUP >= interval:
                    _LAST_CLEANUP = now
                    _cleanup_old_packages(web_app_v2, os.environ.get("AIVF_RETENTION_DAYS", "7"))
