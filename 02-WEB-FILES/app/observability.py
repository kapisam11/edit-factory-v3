"""Small production observability helpers for the Flask dashboard."""
from __future__ import annotations

import logging
import time
import uuid
from flask import Flask, Response, g, jsonify, request
from ai_video_factory.observability_metrics import GLOBAL_METRICS
from ai_video_factory.production_guardrails import SecretRedactionFilter


REQUEST_ID_HEADER = "X-Request-ID"


def install_observability(app: Flask) -> None:
    """Install request correlation and a lightweight health endpoint."""

    @app.before_request
    def attach_request_context() -> None:
        g.request_id = uuid.uuid4().hex
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
        GLOBAL_METRICS.observe_ms("http_request", (time.perf_counter() - started) * 1000.0)
        response.headers[REQUEST_ID_HEADER] = getattr(g, "request_id", "")
        return response

    @app.get("/metrics")
    def metrics_prometheus():
        return Response(
            GLOBAL_METRICS.to_prometheus(),
            mimetype="text/plain; version=0.0.4",
        )

    @app.get("/api/metrics")
    def metrics():
        snapshot = GLOBAL_METRICS.snapshot()
        return jsonify({
            "counters": snapshot.counters,
            "timings_ms_avg": snapshot.timings_ms,
            "generated_at": snapshot.generated_at,
        })

    @app.route("/healthz")
    def healthz():
        started = getattr(g, "request_started_at", time.perf_counter())
        return jsonify({
            "status": "ok",
            "service": "ai-video-factory",
            "request_id": getattr(g, "request_id", ""),
            "path": request.path,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        })
