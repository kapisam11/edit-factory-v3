"""Opt-in concurrent API load harness for a real single-host deployment."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import statistics
import time
import requests


def submit(base_url: str, token: str, index: int) -> tuple[float, int]:
    started = time.perf_counter()
    response = requests.post(
        f"{base_url.rstrip('/')}/api/jobs",
        data={"topic": f"load-test-{index}", "workflow": "v3"},
        headers={"Origin": base_url, "Idempotency-Key": f"load-test-{index}"},
        cookies={"aivf_token": token},
        timeout=30,
    )
    return time.perf_counter() - started, response.status_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--concurrency", type=int, action="append", default=[1, 2, 5, 10, 20])
    parser.add_argument("--requests-per-level", type=int, default=20)
    args = parser.parse_args()

    report = []
    for concurrency in args.concurrency:
        latencies = []
        statuses = []
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [
                pool.submit(submit, args.base_url, args.token, i)
                for i in range(args.requests_per_level)
            ]
            for future in as_completed(futures):
                latency, status = future.result()
                latencies.append(latency)
                statuses.append(status)
        elapsed = max(time.perf_counter() - started, 1e-6)
        report.append({
            "concurrency": concurrency,
            "requests": len(statuses),
            "successes": sum(status in (200, 201, 202) for status in statuses),
            "rate_limited": sum(status == 429 for status in statuses),
            "errors": sum(status >= 500 for status in statuses),
            "throughput_per_second": round(len(statuses) / elapsed, 3),
            "p50_ms": round(statistics.median(latencies) * 1000, 2),
            "max_ms": round(max(latencies) * 1000, 2),
        })
    print(json.dumps({"levels": report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
