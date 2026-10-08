"""Dashboard controls for the autonomous YouTube operating loop."""
from __future__ import annotations

from flask import jsonify, render_template, request

from ai_video_factory.autonomous_youtube import AutonomousManager


def register_autonomous_routes(app) -> None:
    if app.config.get("_AIVF_AUTONOMOUS_ROUTES"):
        return
    manager = AutonomousManager()
    app.config["_AIVF_AUTONOMOUS_ROUTES"] = True

    def require_admin() -> None:
        checker = app.extensions.get("aivf_require_role")
        if callable(checker):
            checker("admin")

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
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"job_id": job_id, "job": manager.store.get(job_id)}), 201

    @app.post("/api/autonomous/approve/<job_id>")
    def autonomous_approve(job_id: str):
        require_admin()
        try:
            manager.approve(job_id, actor=str(request.headers.get("X-AIVF-Actor") or "dashboard-admin"))
        except KeyError:
            return jsonify({"error": "job not found"}), 404
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

__all__ = ["register_autonomous_routes"]
