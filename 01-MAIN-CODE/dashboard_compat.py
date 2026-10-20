"""Dashboard compatibility routes plus queue, cancellation, retention, and secret management."""
import hashlib
import json
import os
from datetime import datetime, timezone
import signal
import shutil
import subprocess
import threading
import time
from pathlib import Path

from flask import jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

SECRET_KEYS = {"groq_key", "model_key", "elevenlabs_key", "diarization_token"}
_DASHBOARD_SECRETS = {key: "" for key in SECRET_KEYS}
_START_LOCK = threading.Lock()
_LIFECYCLE_LOCK = threading.RLock()
_LAST_CLEANUP = 0.0


def _terminate_process_tree(process):
    if not process.is_alive():
        process.join(timeout=1)
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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


def _request_role() -> str:
    from flask import current_app, has_request_context, session
    if not has_request_context():
        return "admin"
    try:
        if not current_app.config.get("_AIVF_AUTH_CONFIGURED") or not session.get("aivf_authenticated"):
            return "admin"
        return str(session.get("aivf_role") or "viewer").strip().lower()
    except RuntimeError:
        return "admin"


def _package_access_allowed(web_app_v3, package) -> bool:
    if not package:
        return False
    if _request_role() == "admin":
        return True
    try:
        with web_app_v3.get_db() as conn:
            rows = conn.execute("SELECT principal, pkg_dir FROM jobs WHERE pkg_dir IS NOT NULL").fetchall()
        from resource_governor import principal_for_request
        principal = principal_for_request(request)
        for row in rows:
            if str(Path(str(row["pkg_dir"])).resolve()) != str(package.resolve()):
                continue
            return str(row["principal"] or "") == principal
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return False


def _resolve_package_file(package, filename):
    """Resolve an existing regular file discovered beneath the package root."""
    if package is None or not isinstance(filename, str):
        return None
    requested = filename.replace("\\", "/")
    if not requested or requested.startswith("/") or requested.startswith("../") or "/../" in requested:
        return None
    root = package.resolve()
    try:
        candidates = package.rglob("*")
    except OSError:
        return None
    for candidate in candidates:
        try:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = candidate.relative_to(package).as_posix()
            if relative != requested:
                continue
            resolved = candidate.resolve()
            resolved.relative_to(root)
            return resolved
        except (OSError, ValueError):
            continue
    return None


def cancel_process(job_id):
    import web_app_v3
    if _request_role() not in {"admin", "editor"}:
        return jsonify({"error": "Editor role required"}), 403
    lookup = getattr(web_app_v3, "authorized_db_get_job", web_app_v3.db_get_job)
    job = lookup(job_id)
    if not job and _request_role() == "admin":
        job = web_app_v3.db_get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    with web_app_v3._active_processes_lock:
        with web_app_v3.get_db() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status='cancelling', step='cancelling', "
                "worker_id=NULL, lease_token=NULL, worker_heartbeat_at=NULL, "
                "updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status NOT IN ('done','error','cancelled','interrupted','cancelling')", (job_id,),
            )
            if cursor.rowcount == 0:
                row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
                if not row:
                    return jsonify({"error": "Job not found"}), 404
                return jsonify({"job_id": job_id, "status": row["status"]}), 409
        # Invalidate the attempt before terminating the process. This closes the
        # race where a worker passes a pre-publish check and then publishes after
        # cancellation has been requested.
        try:
            from dashboard_store import DashboardStore
            DashboardStore(web_app_v3.DB_PATH).revoke_attempt_lease(job_id)
        except Exception:
            web_app_v3.logger.exception("Could not revoke worker lease for cancelled job %s", job_id)

        process = web_app_v3._active_processes.get(job_id)
        if process:
            _terminate_process_tree(process)
            if process.is_alive():
                return jsonify({"error": "Worker termination timed out"}), 503
        web_app_v3._active_processes.pop(job_id, None)
        web_app_v3._runtime_secrets.pop(job_id, None)
        try:
            from dashboard_store import DashboardStore
            DashboardStore(web_app_v3.DB_PATH).release_resources(job_id)
        except Exception:
            web_app_v3.logger.exception("Could not release resources for cancelled job %s", job_id)
        web_app_v3.db_update_job(job_id, status="cancelled", step="cancelled")
        web_app_v3.db_append_log(job_id, "INFO", "Job cancelled")
        try:
            from dashboard_store import DashboardStore
            from resource_governor import principal_for_request
            DashboardStore(web_app_v3.DB_PATH).record_audit_event(
                principal=principal_for_request(request),
                action="JOB_CANCEL",
                resource="job",
                resource_id=job_id,
                remote_addr=request.remote_addr,
                user_agent=request.headers.get("User-Agent"),
                result="success",
            )
        except Exception:
            web_app_v3.logger.exception("Could not persist cancellation audit event for %s", job_id)
        return jsonify({"job_id": job_id, "status": "cancelled"})


def _cleanup_old_packages(web_app_v3, max_age_days):
    try:
        max_age_days = max(0.0, float(max_age_days))
    except (TypeError, ValueError):
        return []
    cutoff = time.time() - max_age_days * 86400
    with web_app_v3.get_db() as conn:
        protected = {str(row["pkg_dir"]) for row in conn.execute("SELECT pkg_dir FROM jobs WHERE status IN ('queued','running','cancelling') AND pkg_dir IS NOT NULL").fetchall()}
    removed = []
    root = web_app_v3.OUTPUT_FOLDER.resolve()
    for child in root.iterdir():
        if child.name == ".work" or not child.is_dir() or str(child.resolve()) in protected:
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed.append(child.name)
        except OSError:
            continue
    return removed


def _reconcile_worker_exit(web_app_v3, job_id, process):
    try:
        exit_code = process.exitcode
        with web_app_v3.get_db() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET status='interrupted', step='interrupted', error=?, error_code='worker_crash', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
                (f"Worker exited unexpectedly with code {exit_code}", job_id),
            )
            cancelled_cursor = conn.execute(
                "UPDATE jobs SET status='cancelled', step='cancelled', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='cancelling'", (job_id,),
            )
        if cursor.rowcount or cancelled_cursor.rowcount:
            try:
                from dashboard_store import DashboardStore
                DashboardStore(web_app_v3.DB_PATH).release_resources(job_id)
            except Exception:
                web_app_v3.logger.exception("Could not release resources after worker exit for %s", job_id)
            level = "INFO" if cancelled_cursor.rowcount else "ERROR"
            message = "Worker exited after cancellation" if cancelled_cursor.rowcount else "Worker exited unexpectedly"
            web_app_v3.db_append_log(job_id, level, message)
    except Exception:
        web_app_v3.logger.exception("Could not reconcile worker exit for %s", job_id)


def _watch_job_process(web_app_v3, job_id, process):
    process.join()
    with _LIFECYCLE_LOCK:
        _reconcile_worker_exit(web_app_v3, job_id, process)
        web_app_v3._active_processes.pop(job_id, None)
        row = web_app_v3.db_get_job(job_id)
        if row and row.get("status") in {"error", "interrupted"}:
            retain = getattr(web_app_v3, "_retain_runtime_secrets_for_retry", None)
            if callable(retain):
                retain(job_id)
            else:
                web_app_v3._runtime_secrets.pop(job_id, None)
        else:
            web_app_v3._runtime_secrets.pop(job_id, None)
        invalidate = getattr(web_app_v3, "_invalidate_package_cache", None)
        if callable(invalidate):
            invalidate()


def _reap_and_dispatch(web_app_v3):
    with _LIFECYCLE_LOCK:
        for job_id, process in list(web_app_v3._active_processes.items()):
            if process.is_alive():
                continue
            process.join(timeout=0)
            _reconcile_worker_exit(web_app_v3, job_id, process)
            web_app_v3._active_processes.pop(job_id, None)
            row = web_app_v3.db_get_job(job_id)
            if row and row.get("status") in {"error", "interrupted"}:
                retain = getattr(web_app_v3, "_retain_runtime_secrets_for_retry", None)
                if callable(retain):
                    retain(job_id)
                else:
                    web_app_v3._runtime_secrets.pop(job_id, None)
            else:
                web_app_v3._runtime_secrets.pop(job_id, None)
        capacity = max(1, int(web_app_v3.get_settings()["max_concurrent_jobs"]))
        while sum(1 for p in web_app_v3._active_processes.values() if p.is_alive()) < capacity:
            with web_app_v3.get_db() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1").fetchone()
            if not row:
                return
            job_id = row["id"]
            params = json.loads(row["params"] or "{}")
            secrets = dict(web_app_v3._runtime_secrets.get(job_id, _DASHBOARD_SECRETS))
            if not web_app_v3._start_job(job_id, params, secrets):
                return
            with web_app_v3.get_db() as conn:
                status = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            if status == "queued":
                return


def register_dashboard_compat(app):
    import web_app_v3
    web_app_v3._watch_job_process = lambda job_id, process: _watch_job_process(web_app_v3, job_id, process)
    original_start_job = web_app_v3._start_job

    def locked_start_job(job_id, params, secrets):
        with _START_LOCK:
            with _LIFECYCLE_LOCK:
                try:
                    return original_start_job(job_id, params, secrets)
                except Exception as exc:
                    web_app_v3._active_processes.pop(job_id, None)
                    web_app_v3._runtime_secrets.pop(job_id, None)
                    try:
                        with web_app_v3.get_db() as conn:
                            cursor = conn.execute(
                                "UPDATE jobs SET status='error', step='failed', error=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','running')",
                                (f"Worker failed to start: {exc}", job_id),
                            )
                        if cursor.rowcount:
                            web_app_v3.db_append_log(job_id, "ERROR", f"Worker failed to start: {exc}")
                    except Exception:
                        web_app_v3.logger.exception("Could not record worker-start failure for %s", job_id)
                    return False

    web_app_v3._start_job = locked_start_job

    def hardened_settings():
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            for key in SECRET_KEYS:
                if key in data:
                    value = str(data.get(key) or "").strip()
                    _DASHBOARD_SECRETS[key] = value
                    web_app_v3.set_runtime_default_secret(key, value)
            for key, value in data.items():
                if key not in SECRET_KEYS:
                    web_app_v3.set_setting(key, value)
                    try:
                        from dashboard_store import DashboardStore
                        from resource_governor import principal_for_request
                        DashboardStore(web_app_v3.DB_PATH).record_audit_event(
                            principal=principal_for_request(request),
                            action="SETTINGS_CHANGED",
                            resource="settings",
                            resource_id=str(key),
                            remote_addr=request.remote_addr,
                            user_agent=request.headers.get("User-Agent"),
                            result="success",
                            metadata={"key": str(key)},
                        )
                    except Exception:
                        web_app_v3.logger.exception("Could not persist settings audit event")
                else:
                    try:
                        from dashboard_store import DashboardStore
                        from resource_governor import principal_for_request
                        DashboardStore(web_app_v3.DB_PATH).record_audit_event(
                            principal=principal_for_request(request),
                            action="SECRET_CONFIGURED",
                            resource="settings",
                            resource_id=str(key),
                            remote_addr=request.remote_addr,
                            user_agent=request.headers.get("User-Agent"),
                            result="success",
                            metadata={"key": str(key), "configured": bool(value)},
                        )
                    except Exception:
                        web_app_v3.logger.exception("Could not persist secret-settings audit event")
        result = dict(web_app_v3.get_settings())
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
        _reap_and_dispatch(web_app_v3)
        with web_app_v3.get_db() as conn:
            queued = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]
            running = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
        return jsonify({"queued": queued, "running": running, "max_concurrent_jobs": int(web_app_v3.get_settings()["max_concurrent_jobs"])})

    @app.route("/api/presets", methods=["GET", "POST", "DELETE"])
    def compat_presets():
        if request.method in {"POST", "DELETE"} and _request_role() != "admin":
            return jsonify({"error": "Admin role required"}), 403

        def read_record(raw_value):
            try:
                raw = json.loads(raw_value)
            except (TypeError, json.JSONDecodeError):
                return {"schema_version": 0, "version": 0, "config": {}, "history": []}
            if (
                isinstance(raw, dict)
                and raw.get("schema_version") == 1
                and isinstance(raw.get("config"), dict)
                and isinstance(raw.get("history"), list)
            ):
                return raw
            return {
                "schema_version": 0,
                "version": 0,
                "config": raw if isinstance(raw, dict) else {},
                "history": [],
            }

        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            name = str(data.get("name", "")).strip()
            config = data.get("config")
            if (
                not name
                or len(name) > 80
                or name in {".", ".."}
                or any(char in name for char in ("/", "\\", "\x00"))
                or not isinstance(config, dict)
            ):
                return jsonify({"error": "Invalid preset name or config"}), 400
            try:
                serialized_config = json.dumps(config, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                return jsonify({"error": "Preset config must contain valid JSON values"}), 400
            if len(serialized_config) > 100_000:
                return jsonify({"error": "Preset config is too large"}), 413

            with web_app_v3.get_db() as conn:
                row = conn.execute("SELECT value FROM settings WHERE key=?", (f"preset:{name}",)).fetchone()
                previous = read_record(row["value"]) if row else read_record("{}")
                version = int(previous.get("version", 0)) + 1
                saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                digest = hashlib.sha256(serialized_config.encode("utf-8")).hexdigest()
                history = list(previous.get("history") or [])
                # Legacy presets become version 1 on their next save.
                if row and not history and previous.get("config"):
                    old_json = json.dumps(previous["config"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    history.append({
                        "version": max(1, int(previous.get("version", 0))),
                        "saved_at": saved_at,
                        "sha256": hashlib.sha256(old_json.encode("utf-8")).hexdigest(),
                        "config": previous["config"],
                        "legacy": True,
                    })
                # Legacy revisions already occupy an ID; continue with the next ID.
                version = max(
                    version,
                    max((int(item.get("version", 0)) for item in history), default=0) + 1,
                )
                history.append({
                    "version": version,
                    "saved_at": saved_at,
                    "sha256": digest,
                    "config": config,
                })
                history = history[-20:]
                record = {
                    "schema_version": 1,
                    "version": version,
                    "config": config,
                    "history": history,
                }
                conn.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (f"preset:{name}", json.dumps(record, ensure_ascii=False, allow_nan=False)),
                )
            return jsonify({"ok": True, "version": version, "history_count": len(history)}), 201

        if request.method == "DELETE":
            data = request.get_json(silent=True) or {}
            name = str(request.args.get("name") or data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "Specify the preset name to delete"}), 400
            with web_app_v3.get_db() as conn:
                removed = conn.execute("DELETE FROM settings WHERE key=?", (f"preset:{name}",)).rowcount
            return jsonify({"ok": bool(removed), "deleted": name if removed else None}), 200

        with web_app_v3.get_db() as conn:
            rows = conn.execute("SELECT key,value FROM settings WHERE key LIKE 'preset:%' ORDER BY key").fetchall()
        output = {}
        for row in rows:
            name = row["key"][7:]
            record = read_record(row["value"])
            config = dict(record.get("config") or {})
            if record.get("schema_version") == 1:
                config["_preset_meta"] = {
                    "schema_version": 1,
                    "version": int(record.get("version", 0)),
                    "history_count": len(record.get("history") or []),
                }
            output[name] = config
        return jsonify(output)

    @app.route("/api/presets/<name>/history", methods=["GET"])
    def compat_preset_history(name):
        with web_app_v3.get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (f"preset:{name}",)).fetchone()
        if row is None:
            return jsonify({"error": "Preset not found"}), 404
        try:
            record = json.loads(row["value"])
        except json.JSONDecodeError:
            return jsonify({"error": "Preset data is damaged"}), 409
        history = record.get("history", []) if isinstance(record, dict) else []
        return jsonify({
            "name": name,
            "version": int(record.get("version", 0)) if isinstance(record, dict) else 0,
            "history": [
                {key: item.get(key) for key in ("version", "saved_at", "sha256", "legacy", "restored_from")}
                for item in history if isinstance(item, dict)
            ],
        })

    @app.route("/api/presets/<name>/restore/<int:version>", methods=["POST"])
    def compat_preset_restore(name, version):
        if _request_role() != "admin":
            return jsonify({"error": "Admin role required"}), 403
        with web_app_v3.get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (f"preset:{name}",)).fetchone()
            if row is None:
                return jsonify({"error": "Preset not found"}), 404
            try:
                record = json.loads(row["value"])
            except json.JSONDecodeError:
                return jsonify({"error": "Preset data is damaged"}), 409
            if not isinstance(record, dict):
                return jsonify({"error": "Preset data is invalid"}), 409
            history = record.get("history", [])
            target = next((item for item in history if isinstance(item, dict) and item.get("version") == version), None)
            if target is None or not isinstance(target.get("config"), dict):
                return jsonify({"error": "Preset revision not found"}), 404
            config = target["config"]
            if not target.get("legacy"):
                source_encoding = json.dumps(config, ensure_ascii=False, allow_nan=False)
                source_digest = hashlib.sha256(source_encoding.encode("utf-8")).hexdigest()
                if source_digest != str(target.get("sha256") or ""):
                    return jsonify({"error": "Preset revision failed its integrity check"}), 409
            latest = max(int(record.get("version", 0)), 0) + 1
            saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            encoded = json.dumps(config, ensure_ascii=False, allow_nan=False)
            history.append({
                "version": latest,
                "saved_at": saved_at,
                "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                "config": config,
                "restored_from": version,
            })
            record.update({"schema_version": 1, "version": latest, "config": config, "history": history[-20:]})
            conn.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(record, ensure_ascii=False, allow_nan=False), f"preset:{name}"))
        return jsonify({"ok": True, "version": latest, "restored_from": version}), 200

    @app.route("/editorial-review/<name>")
    def editorial_review_page(name):
        import web_app_v3
        package = web_app_v3._resolve_package(name)
        if not _package_access_allowed(web_app_v3, package):
            return "Package not found", 404
        return web_app_v3.render_template("editorial_review.html", package_name=package.name)

    @app.route("/api/package/<name>/rating", methods=["GET", "POST"])
    def compat_package_rating(name):
        import web_app_v3
        from ai_video_factory.video_learning import add_manual_rating

        package = web_app_v3._resolve_package(name)
        if not _package_access_allowed(web_app_v3, package):
            return jsonify({"error": "Package not found"}), 404
        if _request_role() not in {"admin", "editor"}:
            return jsonify({"error": "Editor role required"}), 403
        state_dir = Path(os.environ.get("AIVF_STATE_DIR", "state")).expanduser()
        history_path = Path(
            os.environ.get("AIVF_LEARNING_HISTORY_PATH", "").strip()
            or str(state_dir / "learning_history.json")
        ).expanduser()
        if request.method == "GET":
            try:
                payload = json.loads(history_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return jsonify({"available": False, "rating": None, "note": ""}), 200
            except (OSError, json.JSONDecodeError) as exc:
                return jsonify({"error": f"Learning history is unreadable: {type(exc).__name__}"}), 409
            if not isinstance(payload, list):
                return jsonify({"error": "Learning history must be a JSON list"}), 409
            wanted = os.path.normcase(os.path.abspath(str(package.resolve())))
            for item in reversed(payload):
                if not isinstance(item, dict):
                    continue
                item_dir = str(item.get("package_dir") or "")
                if item_dir and os.path.normcase(os.path.abspath(item_dir)) == wanted:
                    return jsonify({
                        "available": True,
                        "rating": item.get("user_rating"),
                        "note": str(item.get("user_feedback") or ""),
                        "feedback_at": item.get("user_feedback_at"),
                    }), 200
            return jsonify({"available": False, "rating": None, "note": ""}), 200

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Rating body must be a JSON object"}), 400
        rating = payload.get("rating")
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            return jsonify({"error": "rating must be an integer from 1 to 5"}), 400
        note = payload.get("note", "")
        if not isinstance(note, str) or len(note) > 1000:
            return jsonify({"error": "note must be text with at most 1000 characters"}), 400

        try:
            saved = add_manual_rating(
                history_path,
                package_dir=str(package.resolve()),
                rating=rating,
                note=note,
            )
        except FileNotFoundError:
            return jsonify({
                "error": "No learning-history file or matching package record exists yet. "
                "Only rate packages that have a saved learning-history record, or configure "
                "AIVF_LEARNING_HISTORY_PATH to the existing history file."
            }), 409
        except KeyError:
            return jsonify({
                "error": "This package is not yet matched to an existing learning-history record."
            }), 409
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"error": f"Could not save channel feedback: {type(exc).__name__}: {exc}"}), 400
        return jsonify({
            "ok": True,
            "package": package.name,
            "rating": int(saved.get("user_rating", rating)),
            "feedback_at": saved.get("user_feedback_at"),
            "message": "Creator feedback saved to the configured local learning history.",
        }), 200

    @app.route("/api/package/<name>/review", methods=["GET", "POST"])
    def compat_package_review(name):
        import web_app_v3
        from ai_video_factory.review_workbench import load_review, save_review

        package = web_app_v3._resolve_package(name)
        if not _package_access_allowed(web_app_v3, package):
            return jsonify({"error": "Package not found"}), 404
        if request.method == "POST" and _request_role() not in {"admin", "editor"}:
            return jsonify({"error": "Editor role required"}), 403
        if request.method == "GET":
            try:
                state = load_review(package)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 409
            from urllib.parse import quote
            response = dict(state)
            video_file = str(state.get("video_file") or "")
            if video_file:
                video_path = (package / video_file).resolve()
                root = package.resolve()
                if video_path.is_file() and video_path.is_relative_to(root):
                    response["video_url"] = (
                        f"/api/package/{quote(package.name, safe='')}/file/"
                        f"{quote(video_file, safe='/')}"
                    )
            response["review_url"] = f"/editorial-review/{quote(package.name, safe='')}"
            return jsonify(response)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Review body must be a JSON object"}), 400
        try:
            state = save_review(package, payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        from urllib.parse import quote
        response = dict(state)
        video_file = str(state.get("video_file") or "")
        if video_file:
            video_path = (package / video_file).resolve()
            root = package.resolve()
            if video_path.is_file() and video_path.is_relative_to(root):
                response["video_url"] = (
                    f"/api/package/{quote(package.name, safe='')}/file/"
                    f"{quote(video_file, safe='/')}"
                )
        response["review_url"] = f"/editorial-review/{quote(package.name, safe='')}"
        return jsonify({"ok": True, "review": response}), 200

    @app.route("/api/package/<name>/review/restore/<int:version>", methods=["POST"])
    def compat_package_review_restore(name, version):
        import web_app_v3
        from ai_video_factory.review_workbench import restore_review

        package = web_app_v3._resolve_package(name)
        if not _package_access_allowed(web_app_v3, package):
            return jsonify({"error": "Package not found"}), 404
        if _request_role() not in {"admin", "editor"}:
            return jsonify({"error": "Editor role required"}), 403
        try:
            state = restore_review(package, version)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        from urllib.parse import quote
        response = dict(state)
        video_file = str(state.get("video_file") or "")
        if video_file:
            video_path = (package / video_file).resolve()
            root = package.resolve()
            if video_path.is_file() and video_path.is_relative_to(root):
                response["video_url"] = (
                    f"/api/package/{quote(package.name, safe='')}/file/"
                    f"{quote(video_file, safe='/')}"
                )
        response["review_url"] = f"/editorial-review/{quote(package.name, safe='')}"
        return jsonify({"ok": True, "review": response}), 200

    @app.route("/api/package/<name>/script", methods=["GET", "POST"])
    def compat_script(name):
        package = web_app_v3._resolve_package(name)
        if not _package_access_allowed(web_app_v3, package):
            return jsonify({"error": "Package not found"}), 404
        if request.method == "POST" and _request_role() not in {"admin", "editor"}:
            return jsonify({"error": "Editor role required"}), 403
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
        package = web_app_v3._resolve_package(name)
        if not _package_access_allowed(web_app_v3, package):
            return jsonify({"error": "Package not found"}), 404
        return jsonify([{"path": path.relative_to(package).as_posix(), "size": path.stat().st_size} for path in package.rglob("*") if path.is_file()])

    @app.route("/api/package/<name>/file/<path:filename>")
    def compat_file(name, filename):
        package = web_app_v3._resolve_package(name)
        if not _package_access_allowed(web_app_v3, package):
            return jsonify({"error": "Package not found"}), 404
        if package is None:
            return jsonify({"error": "Package not found"}), 404
        resolved = _resolve_package_file(package, filename)
        if resolved is None:
            return jsonify({"error": "Package not found"}), 404
        return send_file(resolved)

    @app.route("/api/admin/audit", methods=["GET"])
    def audit_events_admin():
        if _request_role() != "admin":
            return jsonify({"error": "Admin role required"}), 403
        raw_last_id = request.args.get("last_id", "0")
        raw_limit = request.args.get("limit", "200")
        try:
            last_id = max(0, int(raw_last_id))
            limit = max(1, min(1000, int(raw_limit)))
        except (TypeError, ValueError):
            return jsonify({"error": "last_id and limit must be integers"}), 400
        from dashboard_store import DashboardStore
        events = DashboardStore(web_app_v3.DB_PATH).audit_events_since(last_id=last_id, limit=limit)
        return jsonify({
            "events": events,
            "last_id": int(events[-1]["id"]) if events else last_id,
        })

    @app.route("/api/admin/cleanup", methods=["POST"])
    def cleanup_packages_admin():
        if _request_role() != "admin":
            return jsonify({"error": "Admin role required"}), 403
        days = request.args.get("max_age_days", os.environ.get("AIVF_RETENTION_DAYS", "7"))
        removed = _cleanup_old_packages(web_app_v3, days)
        return jsonify({"removed": removed, "count": len(removed)})

    @app.before_request
    def lifecycle_maintenance():
        if request.path.endswith("/preview") or request.path.startswith("/api/package/") or request.path.startswith("/api/packages/"):
            return
        _reap_and_dispatch(web_app_v3)

