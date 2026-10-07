#!/usr/bin/env python3
"""Real target-host job load test for 1/2/5/10/20 concurrent submissions.

This intentionally runs against an already deployed dashboard so production
capacity is measured rather than simulated in SQLite.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import time
from pathlib import Path

import requests


def submit(base_url: str, token: str, fixture: Path, index: int) -> dict[str, object]:
    started = time.perf_counter()
    session = requests.Session()
    login = session.post(
        f"{base_url}/login",
        data={"token": token},
        headers={"Origin": base_url},
        allow_redirects=False,
        timeout=30,
    )
    if login.status_code not in (302, 303):
        raise RuntimeError(f"login failed: {login.status_code}")
    with fixture.open("rb") as handle:
        response = session.post(
            f"{base_url}/api/jobs",
            data={
                "topic": f"load-test-{index}",
                "workflow": "v3",
                "target_seconds": "15",
                "platform": "youtube_shorts",
                "audience": "load test",
                "context": "production load test",
                "bpm": "120",
            },
            files={"raw_video": ("load-test.mp4", handle, "video/mp4")},
            headers={"Origin": base_url},
            timeout=60,
        )
    if response.status_code not in (200, 201, 202):
        raise RuntimeError(f"submit failed: {response.status_code}: {response.text[:500]}")
    payload = response.json()
    return {
        "job_id": payload.get("job_id"),
        "submit_ms": round((time.perf_counter() - started) * 1000, 2),
    }


def run_level(base_url: str, token: str, fixture: Path, concurrency: int) -> dict[str, object]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(submit, base_url, token, fixture, i) for i in range(concurrency)]
        results = []
        errors = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    latencies = [float(item["submit_ms"]) for item in results]
    return {
        "concurrency": concurrency,
        "submitted": len(results),
        "errors": errors,
        "p50_submit_ms": round(statistics.median(latencies), 2) if latencies else None,
        "max_submit_ms": round(max(latencies), 2) if latencies else None,
        "job_ids": [item["job_id"] for item in results if item.get("job_id")],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--levels", default="1,2,5,10,20")
    args = parser.parse_args()
    if not args.fixture.is_file():
        raise SystemExit(f"fixture does not exist: {args.fixture}")
    levels = [int(item) for item in args.levels.split(",") if item.strip()]
    if any(level < 1 or level > 20 for level in levels):
        raise SystemExit("load levels must be between 1 and 20")
    report = [
        run_level(args.base_url.rstrip("/"), args.token, args.fixture, level)
        for level in levels
    ]
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if any(item["errors"] for item in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
