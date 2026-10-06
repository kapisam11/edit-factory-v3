"""Production request correlation, metrics and health endpoints."""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from flask import Flask, Response, g, jsonify, request

from ai_video_factory.observability_metrics import GLOBAL_METRICS
from ai_video_factory.production_guardrails import SecretRedactionFilter
from ai_video_factory.system_diagnostics import diagnostics_report

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def install_observability(app: Flask) -> None:
    """Install request correlation, metrics and binary health/readiness endpoints."""

    state_dir = str(
        app.config.get("AIVF_STATE_DIR")
        or os.environ.get("AIVF_STATE_DIR", "")
        or Path(app.root_path).parent / "state"
    )
    app.config["AIVF_DB_PATH"] = app.config.get("AIVF_DB_PATH") or str(
        Path(state_dir) / "jobs.db"
    )

    redaction_filter = SecretRedactionFilter()
    for logger in (app.logger, logging.getLogger()):
        for handler in logger.handlers:
            if not any(
                isinstance(existing, SecretRedactionFilter)
                for existing in handler.filters
            ):
                handler.addFilter(redaction_filter)

    def refresh_runtime_gauges() -> None:
        try:
            db_path = str(app.config["AIVF_DB_PATH"])
            with sqlite3.connect(db_path, timeout=2) as conn:
                queued = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM jobs WHERE status='queued'"
                    ).fetchone()[0]
                )
                running = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM jobs "
                        "WHERE status IN ('running','cancelling')"
                    ).fetchone()[0]
                )
            GLOBAL_METRICS.set_gauge("queue_depth", queued)
            GLOBAL_METRICS.set_gauge("jobs_running", running)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            pass

        try:
            free_bytes = int(shutil.disk_usage(state_dir).free)
            GLOBAL_METRICS.set_gauge("disk_free_bytes", float(free_bytes))
        except OSError:
            pass

    @app.before_request
    def attach_request_context() -> None:
        supplied = request.headers.get(REQUEST_ID_HEADER, "").strip()
        g.request_id = (
            supplied if _REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex
        )
        g.request_started_at = time.perf_counter()

    @app.after_request
    def add_request_headers(response: Response) -> Response:
        started = getattr(g, "request_started_at", time.perf_counter())
        GLOBAL_METRICS.increment(
            f"http_requests_total:{request.method}:{request.endpoint or 'unknown'}"
        )
        GLOBAL_METRICS.increment(f"http_response_total:{response.status_code}")
        GLOBAL_METRICS.observe_ms(
            "http_request",
            (time.perf_counter() - started) * 1000.0,
        )
        refresh_runtime_gauges()
        response.headers[REQUEST_ID_HEADER] = getattr(g, "request_id", "")
        return response

    @app.get("/metrics")
    def prometheus_metrics():
        refresh_runtime_gauges()
        return Response(
            GLOBAL_METRICS.prometheus(),
            mimetype="text/plain; version=0.0.4",
        )

    @app.get("/api/metrics")
    def metrics():
        refresh_runtime_gauges()
        snapshot = GLOBAL_METRICS.snapshot()
        return jsonify(
            {
                "counters": snapshot.counters,
                "gauges": snapshot.gauges,
                "timings_ms_avg": snapshot.timings_ms,
                "timing_percentiles_ms": snapshot.timing_percentiles_ms,
                "generated_at": snapshot.generated_at,
            }
        )

    @app.get("/readyz")
    def readyz():
        report = diagnostics_report(
            required_tools=("ffmpeg", "ffprobe"),
            directories=[state_dir],
        )
        try:
            minimum_free = max(
                0,
                int(
                    os.environ.get(
                        "AIVF_MIN_FREE_DISK_MB",
                        str(512),
                    )
                ),
            ) * 1024 * 1024
        except ValueError:
            minimum_free = 512 * 1024 * 1024

        directory = (report.get("directories") or [{}])[0]
        free_bytes = int(directory.get("free_bytes") or 0)
        if free_bytes < minimum_free:
            report["ok"] = False

        status = 200 if report["ok"] else 503
        return jsonify(
            {
                "status": "ready" if status == 200 else "not_ready",
                "request_id": getattr(g, "request_id", ""),
            }
        ), status

    @app.get("/healthz")
    def healthz():
        started = getattr(g, "request_started_at", time.perf_counter())
        return jsonify(
            {
                "status": "ok",
                "service": "ai-video-factory",
                "request_id": getattr(g, "request_id", ""),
                "elapsed_ms": round(
                    (time.perf_counter() - started) * 1000,
                    3,
                ),
            }
        )
