#!/usr/bin/env python3
"""Black-box Edit Factory V3 load test.

Usage:
  python run_load_test.py --base-url http://127.0.0.1:5000 --fixture sample.mp4 --token TOKEN

The harness intentionally talks only to public dashboard APIs so it can be used
against a staging container or a target host. It emits JSON, CSV and a tiny SVG
throughput/failure graph without adding plotting dependencies.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time
from typing import Any
import requests


@dataclass
class JobResult:
    submitted: bool
    submit_latency_ms: float
    total_latency_ms: float
    queue_latency_ms: float | None
    status: str
    error: str = ""


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 3)


def _login(base_url: str, token: str) -> requests.Session:
    session = requests.Session()
    response = session.post(
        f"{base_url}/login",
        data={"token": token},
        headers={"Origin": base_url},
        allow_redirects=False,
        timeout=20,
    )
    if response.status_code not in (302, 303):
        raise RuntimeError(f"login failed: HTTP {response.status_code}")
    return session


def _submit_one(base_url: str, token: str, fixture: Path, index: int) -> JobResult:
    session = _login(base_url, token)
    started = time.perf_counter()
    submit_started = started
    try:
        with fixture.open("rb") as handle:
            response = session.post(
                f"{base_url}/api/jobs",
                data={
                    "topic": f"load-test-{index}",
                    "target_seconds": "8",
                    "workflow": "v3",
                    "platform": "youtube_shorts",
                    "audience": "load test",
                    "context": "deterministic load test",
                    "rights_basis": "owned",
                    "bpm": "120",
                    "enable_ocr": "false",
                    "enable_object_detection": "false",
                    "enable_diarization": "false",
                },
                files={"raw_video": (fixture.name, handle, "video/mp4")},
                headers={"Origin": base_url, "Idempotency-Key": f"load-test-{time.time_ns()}-{index}"},
                timeout=45,
            )
        submit_latency = (time.perf_counter() - submit_started) * 1000.0
        if response.status_code not in (200, 201, 202):
            return JobResult(False, submit_latency, submit_latency, None, "submit_failed", response.text[:500])

        payload = response.json()
        job_id = str(payload.get("job_id") or "")
        if not job_id:
            return JobResult(False, submit_latency, submit_latency, None, "submit_failed", "missing job_id")

        terminal = "queued"
        queue_latency = None
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            detail = session.get(f"{base_url}/api/jobs/{job_id}", timeout=20)
            detail.raise_for_status()
            data = detail.json()
            terminal = str(data.get("status") or "unknown")
            if terminal == "running" and queue_latency is None:
                queue_latency = (time.perf_counter() - started) * 1000.0 - submit_latency
            if terminal in {"done", "error", "interrupted", "cancelled"}:
                error = str(data.get("error") or "")
                return JobResult(
                    True,
                    round(submit_latency, 3),
                    round((time.perf_counter() - started) * 1000.0, 3),
                    round(queue_latency, 3) if queue_latency is not None else None,
                    terminal,
                    error[:500],
                )
            time.sleep(0.5)
        return JobResult(True, round(submit_latency, 3), round((time.perf_counter() - started) * 1000.0, 3), queue_latency, "timeout", "job did not reach terminal state")
    except Exception as exc:
        return JobResult(False, round((time.perf_counter() - submit_started) * 1000.0, 3), round((time.perf_counter() - started) * 1000.0, 3), None, "exception", str(exc)[:500])
    finally:
        session.close()


def _metrics(base_url: str, token: str) -> dict[str, Any]:
    try:
        session = _login(base_url, token)
        response = session.get(f"{base_url}/api/metrics", timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception:
        return {}
    finally:
        try:
            session.close()
        except Exception:
            pass


def _write_svg(rows: list[dict[str, Any]], target: Path) -> None:
    width, height = 900, 520
    margin = 70
    max_x = max((float(row["concurrency"]) for row in rows), default=1.0)
    max_y = max((float(row["throughput_jobs_per_minute"]) for row in rows), default=1.0)
    max_f = max((float(row["failure_rate"]) for row in rows), default=1.0)

    def x(value: float) -> float:
        return margin + (value / max_x) * (width - 2 * margin)

    def y(value: float) -> float:
        return height - margin - (value / max_y) * (height - 2 * margin)

    def yf(value: float) -> float:
        return height - margin - (value / max_f if max_f else 0.0) * (height - 2 * margin)

    throughput_points = " ".join(f"{x(float(r['concurrency'])):.1f},{y(float(r['throughput_jobs_per_minute'])):.1f}" for r in rows)
    failure_points = " ".join(f"{x(float(r['concurrency'])):.1f},{yf(float(r['failure_rate'])):.1f}" for r in rows)

    body = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{margin}" y="25" font-size="20">Edit Factory V3 load test</text>',
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="black"/>',
        f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="black"/>',
        f'<polyline points="{throughput_points}" fill="none" stroke="black" stroke-width="3"/>',
        f'<polyline points="{failure_points}" fill="none" stroke="gray" stroke-width="3" stroke-dasharray="8 6"/>',
        f'<text x="{margin}" y="{height-15}" font-size="12">concurrent jobs</text>',
        f'<text x="{margin}" y="45" font-size="12">throughput / failure-rate (see JSON for scales)</text>',
        '</svg>',
    ]
    target.write_text("\n".join(body) + "\n", encoding="utf-8")


def run(base_url: str, token: str, fixture: Path, levels: list[int]) -> dict[str, Any]:
    rows = []
    for concurrency in levels:
        started = time.perf_counter()
        results: list[JobResult] = []
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(_submit_one, base_url, token, fixture, index)
                for index in range(concurrency)
            ]
            for future in as_completed(futures):
                results.append(future.result())
        elapsed = max(0.001, time.perf_counter() - started)
        failures = sum(result.status not in {"done"} for result in results)
        submission_failures = sum(not result.submitted for result in results)
        submit_values = [r.submit_latency_ms for r in results]
        total_values = [r.total_latency_ms for r in results]
        queue_values = [r.queue_latency_ms for r in results if r.queue_latency_ms is not None]
        metrics = _metrics(base_url, token)
        rows.append({
            "concurrency": concurrency,
            "elapsed_seconds": round(elapsed, 3),
            "throughput_jobs_per_minute": round(len(results) / elapsed * 60.0, 3),
            "failure_rate": round(failures / max(1, len(results)), 4),
            "submission_failure_rate": round(submission_failures / max(1, len(results)), 4),
            "submit_latency_p50_ms": _percentile(submit_values, 0.50),
            "submit_latency_p95_ms": _percentile(submit_values, 0.95),
            "submit_latency_p99_ms": _percentile(submit_values, 0.99),
            "total_latency_p50_ms": _percentile(total_values, 0.50),
            "total_latency_p95_ms": _percentile(total_values, 0.95),
            "total_latency_p99_ms": _percentile(total_values, 0.99),
            "queue_latency_p50_ms": _percentile(queue_values, 0.50),
            "queue_latency_p95_ms": _percentile(queue_values, 0.95),
            "queue_latency_p99_ms": _percentile(queue_values, 0.99),
            "db_lock_retries": metrics.get("counters", {}).get("sqlite_lock_retries", 0),
            "queue_depth": metrics.get("gauges", {}).get("queue_depth"),
            "disk_free_bytes": metrics.get("gauges", {}).get("disk_free_bytes"),
            "statuses": {status: sum(r.status == status for r in results) for status in sorted({r.status for r in results})},
            "errors": [r.error for r in results if r.error][:10],
        })
    return {"levels": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--token", required=True)
    parser.add_argument("--levels", default="1,2,5,10,20")
    parser.add_argument("--output", default="load-test-results")
    args = parser.parse_args(argv)

    fixture = args.fixture.resolve()
    if not fixture.is_file():
        raise SystemExit(f"fixture not found: {fixture}")
    levels = sorted({int(value.strip()) for value in args.levels.split(",") if value.strip() and int(value.strip()) > 0})
    if not levels:
        raise SystemExit("levels must contain at least one positive integer")

    report = run(args.base_url.rstrip("/"), args.token, fixture, levels)
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    (target / "results.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with (target / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "concurrency", "elapsed_seconds", "throughput_jobs_per_minute",
            "failure_rate", "submission_failure_rate",
            "submit_latency_p50_ms", "submit_latency_p95_ms", "submit_latency_p99_ms",
            "total_latency_p50_ms", "total_latency_p95_ms", "total_latency_p99_ms",
            "queue_latency_p50_ms", "queue_latency_p95_ms", "queue_latency_p99_ms",
            "db_lock_retries", "queue_depth", "disk_free_bytes",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fieldnames} for row in report["levels"])

    _write_svg(report["levels"], target / "throughput.svg")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
