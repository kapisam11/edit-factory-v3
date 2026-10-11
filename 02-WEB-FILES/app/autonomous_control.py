"""Dashboard controls for the autonomous YouTube operating loop."""
from __future__ import annotations

from flask import abort, jsonify, render_template, request

from ai_video_factory.autonomous_youtube import AutonomousManager


def register_autonomous_routes(app) -> None:
    if app.config.get("_AIVF_AUTONOMOUS_ROUTES"):
        return
    manager = AutonomousManager()
    app.config["_AIVF_AUTONOMOUS_ROUTES"] = True

    def require_admin() -> None:
        checker = app.extensions.get("aivf_require_role")
        if not callable(checker):
            # Never expose autonomous controls if the host forgot to register
            # the production authorization boundary.
            abort(503, description="autonomous authorization is unavailable")
        result = checker("admin")
        if result is False:
            abort(403)

    @app.get("/autonomous")
    def autonomous_page():
        require_admin()
        return render_template("autonomous.html")

    @app.get("/api/autonomous/status")
    def autonomous_status():
        require_admin()
        return jsonify(manager.status())

    @app.get("/api/autonomous/queue")
    def autonomous_queue():
        require_admin()
        limit = request.args.get("limit", default="50", type=int)
        return jsonify(manager.store.list_jobs(limit=limit))

    @app.post("/api/autonomous/enqueue")
    def autonomous_enqueue():
        require_admin()
        payload = request.get_json(silent=True) or {}
        topic = str(payload.get("topic") or "").strip()
        if not topic:
            return jsonify({"error": "topic is required"}), 400
        try:
            job_id = manager.enqueue(
                topic,
                platform=str(payload.get("platform") or "youtube_shorts"),
                scheduled_at=payload.get("publish_at"),
                priority=int(payload.get("priority") or 0),
                publish_requested=bool(payload.get("publish_requested", True)),
                options=payload.get("options") if isinstance(payload.get("options"), dict) else {},
            )
        except (ValueError, RuntimeError, TypeError) as exc:
            app.logger.warning("autonomous enqueue rejected: %s", exc)
            return jsonify({
                "error": "unable to enqueue autonomous job",
                "code": "AUTONOMY_ENQUEUE_FAILED",
            }), 400
        return jsonify({"job_id": job_id, "job": manager.store.get(job_id)}), 201

    @app.post("/api/autonomous/approve/<job_id>")
    def autonomous_approve(job_id: str):
        require_admin()
        payload = request.get_json(silent=True) or {}
        try:
            manager.approve(
                job_id,
                actor=str(request.headers.get("X-AIVF-Actor") or "dashboard-admin"),
                disclosure_acknowledged=payload.get("disclosure_acknowledged") is True,
            )
        except KeyError:
            return jsonify({"error": "job not found"}), 404
        except ValueError as exc:
            app.logger.warning("autonomous approval rejected for job %s: %s", job_id, exc)
            return jsonify({
                "error": "unable to approve autonomous job",
                "code": "AUTONOMY_APPROVAL_REJECTED",
            }), 400
        return jsonify(manager.store.get(job_id))

    @app.post("/api/autonomous/pause")
    def autonomous_pause():
        require_admin()
        payload = request.get_json(silent=True) or {}
        manager.pause(str(payload.get("reason") or "dashboard pause"))
        return jsonify(manager.status())

    @app.post("/api/autonomous/resume")
    def autonomous_resume():
        require_admin()
        manager.resume()
        return jsonify(manager.status())

    @app.post("/api/autonomous/emergency-stop")
    def autonomous_emergency_stop():
        require_admin()
        payload = request.get_json(silent=True) or {}
        manager.emergency_stop(str(payload.get("reason") or "dashboard emergency stop"))
        return jsonify(manager.status())

    @app.post("/api/autonomous/clear-emergency-stop")
    def autonomous_clear_emergency_stop():
        require_admin()
        manager.clear_emergency_stop()
        return jsonify(manager.status())

    @app.post("/api/autonomous/run-once")
    def autonomous_run_once():
        require_admin()
        payload = request.get_json(silent=True) or {}
        seeds = payload.get("seed_topics") or []
        if not isinstance(seeds, list):
            return jsonify({"error": "seed_topics must be a list"}), 400
        return jsonify(manager.run_once(seed_topics=[str(item) for item in seeds if str(item).strip()]))


    def publisher_store():
        import os
        from pathlib import Path
        from ai_video_factory.publishing_queue import PublisherStore

        state_dir = Path(str(app.config.get("AIVF_STATE_DIR") or os.environ.get("AIVF_STATE_DIR", "./state"))).expanduser().resolve()
        db_path = Path(os.environ.get("AIVF_PUBLISH_DB", str(state_dir / "multi-platform-publisher.sqlite"))).expanduser().resolve()
        return PublisherStore(db_path)

    @app.get("/publisher")
    def publisher_page():
        require_admin()
        return render_template("publisher.html")

    @app.get("/api/publisher/status")
    def publisher_status():
        require_admin()
        import os
        import shutil
        from pathlib import Path
        from ai_video_factory.publishing_queue import configured_platforms

        store = publisher_store()
        state_dir = Path(str(app.config.get("AIVF_STATE_DIR") or os.environ.get("AIVF_STATE_DIR", "./state"))).expanduser().resolve()
        try:
            disk = shutil.disk_usage(state_dir)
            disk_info = {"free_bytes": disk.free, "total_bytes": disk.total}
        except OSError:
            disk_info = {"free_bytes": None, "total_bytes": None}
        jobs = store.list_jobs(200)
        published = [job for job in jobs if job.get("state") == "published"]
        latest = max(published, key=lambda job: float(job.get("published_at") or 0), default=None)
        return jsonify({
            "publish_enabled": os.environ.get("AIVF_MULTI_PLATFORM_PUBLISH", "0").strip() == "1",
            "public_publishing_enabled": os.environ.get("AIVF_PUBLISH_ALLOW_PUBLIC", "0").strip() == "1",
            "configured_platforms": configured_platforms(),
            "queue_counts": store.counts(),
            "source_counts": store.source_summary(),
            "published_today_utc": store.published_today(),
            "expected_posts_per_day": max(0, int(os.environ.get("AIVF_PUBLISH_EXPECTED_PER_DAY", "1"))),
            "deadline_utc": os.environ.get("AIVF_PUBLISH_DEADLINE_UTC", ""),
            "poll_seconds": max(1, int(os.environ.get("AIVF_PUBLISH_POLL_SECONDS", "15"))),
            "disk": disk_info,
            "last_success": {key: latest.get(key) for key in ("platform", "account_id", "remote_id", "remote_url", "published_at")} if latest else None,
            "jobs": jobs[:50],
        })

    @app.get("/api/publisher/jobs/<job_id>/events")
    def publisher_job_events(job_id: str):
        require_admin()
        store = publisher_store()
        if store.get_job(job_id) is None:
            return jsonify({"error": "job not found", "code": "PUBLISH_JOB_NOT_FOUND"}), 404
        return jsonify({"job_id": job_id, "events": store.events(job_id, request.args.get("limit", default=100, type=int))})

    @app.post("/api/publisher/jobs/<job_id>/retry")
    def publisher_retry_job(job_id: str):
        require_admin()
        origin = request.headers.get("Origin", "").rstrip("/")
        if not origin or origin != request.host_url.rstrip("/"):
            return jsonify({"error": "same-origin request required", "code": "CROSS_ORIGIN_REJECTED"}), 403
        try:
            publisher_store().retry_job(job_id)
        except KeyError:
            return jsonify({"error": "job not found", "code": "PUBLISH_JOB_NOT_FOUND"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc), "code": "PUBLISH_RETRY_REJECTED"}), 409
        return jsonify({"job_id": job_id, "state": "ready"})

__all__ = ["register_autonomous_routes"]
