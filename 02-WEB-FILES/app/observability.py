"""Small production observability helpers for the Flask dashboard."""
from __future__ import annotations

from flask import Flask, Response, g, jsonify, request
import time
import uuid


REQUEST_ID_HEADER = "X-Request-ID"


def install_observability(app: Flask) -> None:
    """Install request correlation and a lightweight health endpoint."""

    @app.before_request
    def attach_request_context() -> None:
        g.request_id = uuid.uuid4().hex
        g.request_started_at = time.perf_counter()

    @app.after_request
    def add_request_headers(response: Response) -> Response:
        response.headers[REQUEST_ID_HEADER] = getattr(g, "request_id", "")
        return response

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
