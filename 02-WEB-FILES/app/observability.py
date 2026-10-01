"""Small production observability helpers for the Flask dashboard."""
from __future__ import annotations

import logging
import os
import re
import time
import uuid
from pathlib import Path

from flask import Flask, Response, g, jsonify, request
from ai_video_factory.observability_metrics import GLOBAL_METRICS
from ai_video_factory.system_diagnostics import diagnostics_report
from ai_video_factory.production_guardrails import SecretRedactionFilter

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def install_observability(app: Flask) -> None:
    """Install request correlation and lightweight health endpoints."""

    @app.before_request
    def attach_request_context() -> None:
        supplied = request.headers.get(REQUEST_ID_HEADER, "").strip()
        g.request_id = supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
        g.request_started_at = time.perf_counter()

    redaction_filter = SecretRedactionFilter()
    for logger in (app.logger, logging.getLogger()):
        for handler in logger.handlers:
            if not any(isinstance(existing, SecretRedactionFilter) for existing in handler.filters):
                handler.addFilter(redaction_filter)

    @app.after_request
    def add_request_headers(response: Response) -> Response:
        started = getattr(g, "request_started_at", time.perf_counter())
        GLOBAL_METRICS.increment(f"http_requests_total:{request.method}:{request.endpoint or 'unknown'}")
        GLOBAL_METRICS.increment(f"http_response_total:{response.status_code}")
        GLOBAL_METRICS.observe_ms("http_request", (time.perf_counter() - started) * 1000.0)
        response.headers[REQUEST_ID_HEADER] = getattr(g, "request_id", "")
        return response

    @app.get("/metrics")
    def prometheus_metrics():
        return Response(GLOBAL_METRICS.prometheus(), mimetype="text/plain; version=0.0.4")

    @app.get("/api/metrics")
    def metrics():
        snapshot = GLOBAL_METRICS.snapshot()
        return jsonify({"counters": snapshot.counters, "timings_ms_avg": snapshot.timings_ms, "generated_at": snapshot.generated_at})

    @app.route("/readyz")
    def readyz():
        state_dir = str(app.config.get("AIVF_STATE_DIR") or os.environ.get("AIVF_STATE_DIR", "").strip())
        if not state_dir:
            state_dir = str(Path(app.root_path).parent / "state")
        report = diagnostics_report(required_tools=("ffmpeg", "ffprobe"), directories=[state_dir])
        try:
            minimum_free = max(0, int(os.environ.get("AIVF_MIN_FREE_DISK_MB", "512"))) * 1024 * 1024
        except ValueError:
            minimum_free = 512 * 1024 * 1024
        directory = (report.get("directories") or [{}])[0]
        free_bytes = int(directory.get("free_bytes") or 0)
        if free_bytes < minimum_free:
            report["ok"] = False
            report.setdefault("checks", []).append({"key": "minimum_free_disk", "ok": False, "detail": f"free disk {free_bytes} bytes is below required {minimum_free} bytes"})
        status = 200 if report["ok"] else 503
        # Public probes expose only the binary readiness contract. All diagnostic
        # details remain behind authenticated APIs, including filesystem paths.
        return jsonify({
            "status": "ready" if status == 200 else "not_ready",
            "request_id": getattr(g, "request_id", ""),
        }), status

    @app.route("/healthz")
    def healthz():
        started = getattr(g, "request_started_at", time.perf_counter())
        return jsonify({"status": "ok", "service": "ai-video-factory", "request_id": getattr(g, "request_id", ""), "path": request.path, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
