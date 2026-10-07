#!/usr/bin/env python3
"""Single-host Edit Factory load harness.

Measures submission latency, queue latency, completion latency, failures, DB locks,
and host resource samples at controlled concurrency levels. It does not bypass
application admission limits; the goal is to measure the configured system under load.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

import requests

CONCURRENCIES = (1, 2, 5, 10, 20)


@dataclass
class Sample:
    concurrency: int
    submitted: int
    accepted: int
    completed: int
    failed: int
    rejected: int
    submission_p50_ms: float | None
    submission_p95_ms: float | None
    queue_p50_ms: float | None
    queue_p95_ms: float | None
    total_p50_ms: float | None
    total_p95_ms: float | None
    failures_per_second: float
    db_lock_count: int


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 3)


def db_lock_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(db_path, timeout=5) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM job_events "
                "WHERE details LIKE '%database is locked%'"
            ).fetchone()
            return int(row[0] or 0)
    except sqlite3.Error:
        return 0


def submit_and_wait(
    base_url: str,
    token: str,
    fixture: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    session = requests.Session()
    start = time.perf_counter()
    login = session.post(
        f"{base_url}/login",
        data={"token": token},
        headers={"Origin": base_url},
        allow_redirects=False,
        timeout=15,
    )
    if login.status_code not in (302, 303):
        return {"accepted": False, "rejected": True, "submission_ms": (time.perf_counter() - start) * 1000}

    for cookie in login.cookies:
        session.cookies.set(cookie.name, cookie.value, path=cookie.path or "/", secure=False)

    idem = f"load-{time.time_ns()}-{threading.get_ident()}"
    with fixture.open("rb") as handle:
        submitted_at = time.perf_counter()
        response = session.post(
            f"{base_url}/api/jobs",
            data={
                "topic": "load test",
                "workflow": "v3",
                "target_seconds": "8",
                "platform": "youtube_shorts",
                "audience": "load test",
                "context": "deterministic load test",
                "rights_basis": "owned",
                "enable_ocr": "false",
                "enable_object_detection": "false",
                "enable_diarization": "false",
            },
            files={"raw_video": ("load.mp4", handle, "video/mp4")},
            headers={"Origin": base_url, "Idempotency-Key": idem},
            timeout=30,
        )
    submission_ms = (time.perf_counter() - submitted_at) * 1000
    if response.status_code not in (200, 201, 202):
        return {
            "accepted": False,
            "rejected": True,
            "submission_ms": submission_ms,
            "status_code": response.status_code,
        }

    job_id = str(response.json().get("job_id") or "")
    if not job_id:
        return {"accepted": False, "rejected": True, "submission_ms": submission_ms}

    while time.perf_counter() - submitted_at < timeout_seconds:
        payload = session.get(f"{base_url}/api/jobs/{job_id}/status", timeout=15).json()
        status = payload.get("status")
        if status == "done":
            total_ms = (time.perf_counter() - submitted_at) * 1000
            return {
                "accepted": True,
                "completed": True,
                "submission_ms": submission_ms,
                "queue_ms": max(0.0, total_ms - submission_ms),
                "total_ms": total_ms,
            }
        if status in {"error", "interrupted", "cancelled"}:
            total_ms = (time.perf_counter() - submitted_at) * 1000
            return {
                "accepted": True,
                "failed": True,
                "submission_ms": submission_ms,
                "queue_ms": max(0.0, total_ms - submission_ms),
                "total_ms": total_ms,
                "status": status,
            }
        time.sleep(1)

    return {
        "accepted": True,
        "failed": True,
        "submission_ms": submission_ms,
        "status": "timeout",
    }


def run_level(
    base_url: str,
    token: str,
    fixture: Path,
    concurrency: int,
    jobs: int,
    timeout_seconds: float,
    db_path: Path | None,
) -> Sample:
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(submit_and_wait, base_url, token, fixture, timeout_seconds)
            for _ in range(jobs)
        ]
        for future in as_completed(futures):
            results.append(future.result())

    submission = [float(r["submission_ms"]) for r in results if r.get("submission_ms") is not None]
    queue = [float(r["queue_ms"]) for r in results if r.get("queue_ms") is not None]
    total = [float(r["total_ms"]) for r in results if r.get("total_ms") is not None]
    accepted = sum(bool(r.get("accepted")) for r in results)
    completed = sum(bool(r.get("completed")) for r in results)
    failed = sum(bool(r.get("failed")) for r in results)
    rejected = sum(bool(r.get("rejected")) for r in results)
    elapsed = max(0.001, time.perf_counter() - started)

    return Sample(
        concurrency=concurrency,
        submitted=len(results),
        accepted=accepted,
        completed=completed,
        failed=failed,
        rejected=rejected,
        submission_p50_ms=percentile(submission, 0.50),
        submission_p95_ms=percentile(submission, 0.95),
        queue_p50_ms=percentile(queue, 0.50),
        queue_p95_ms=percentile(queue, 0.95),
        total_p50_ms=percentile(total, 0.50),
        total_p95_ms=percentile(total, 0.95),
        failures_per_second=round(failed / elapsed, 4),
        db_lock_count=db_lock_count(db_path) if db_path else 0,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", default=os.environ.get("AIVF_DASHBOARD_TOKEN", ""))
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--concurrency", nargs="+", type=int, default=list(CONCURRENCIES))
    parser.add_argument("--jobs-per-level", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=900)
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)

    if not args.token:
        raise SystemExit("--token or AIVF_DASHBOARD_TOKEN is required")
    fixture = Path(args.fixture)
    if not fixture.is_file():
        raise SystemExit(f"fixture not found: {fixture}")

    samples = []
    for concurrency in args.concurrency:
        if concurrency < 1:
            raise SystemExit("concurrency values must be positive")
        sample = run_level(
            args.base_url.rstrip("/"),
            args.token,
            fixture,
            concurrency,
            max(1, args.jobs_per_level),
            max(30.0, args.timeout_seconds),
            Path(args.db) if args.db else None,
        )
        samples.append(asdict(sample))
        print(json.dumps(samples[-1], sort_keys=True))

    payload = {
        "generated_at": time.time(),
        "base_url": args.base_url,
        "samples": samples,
        "concurrency_profile": {
            "max_completed_jobs": max((item["completed"] for item in samples), default=0),
            "max_failure_rate": max(
                (
                    item["failed"] / item["submitted"]
                    for item in samples
                    if item["submitted"]
                ),
                default=0.0,
            ),
        },
    }
    Path(args.output).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
