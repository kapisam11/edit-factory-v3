from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time

from dashboard_store import DashboardStore

CONCURRENCIES = (1, 2, 5, 10, 20)


def _run_level(tmp_path: Path, concurrency: int) -> dict[str, float | int]:
    db = tmp_path / f"load-{concurrency}.db"
    store = DashboardStore(db)
    store.ensure_indexes()
    started = time.perf_counter()

    def submit(index: int) -> str:
        job_id = f"job-{concurrency}-{index}"
        store.insert_job(
            job_id,
            "load test",
            {"topic": "load test", "_principal": f"principal-{index}"},
            max_queued_jobs=concurrency * 2,
            principal=f"principal-{index}",
        )
        return job_id

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        jobs = list(pool.map(submit, range(concurrency)))

    elapsed = max(1e-9, time.perf_counter() - started)
    with sqlite3.connect(db) as conn:
        count = int(conn.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0])

    assert count == concurrency
    return {
        "concurrency": concurrency,
        "jobs": len(jobs),
        "elapsed_seconds": round(elapsed, 6),
        "requests_per_second": round(concurrency / elapsed, 3),
    }


def test_sqlite_admission_load_matrix(tmp_path):
    results = [_run_level(tmp_path, level) for level in CONCURRENCIES]
    for result in results:
        assert result["jobs"] == result["concurrency"]
        assert result["elapsed_seconds"] > 0
