"""Low-dependency persistent Prometheus-compatible metrics for the single-host runtime."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class MetricSnapshot:
    counters: dict[str, int]
    gauges: dict[str, float]
    timings_ms: dict[str, float]
    timing_percentiles_ms: dict[str, dict[str, float]]
    generated_at: float


class MetricsRegistry:
    """Thread/process-safe metrics registry backed by SQLite when configured."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        configured = storage_path or os.environ.get("AIVF_METRICS_DB", "").strip()
        self.storage_path = Path(configured).resolve() if configured else None
        self._lock = threading.RLock()
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._timings: dict[str, list[float]] = {}
        if self.storage_path is not None:
            self._init_db()

    def _connect(self) -> sqlite3.Connection:
        if self.storage_path is None:
            raise RuntimeError("persistent metrics storage is not configured")
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.storage_path), timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metric_counters (
                    name TEXT PRIMARY KEY,
                    value INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS metric_gauges (
                    name TEXT PRIMARY KEY,
                    value REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS metric_timings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    value_ms REAL NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_metric_timings_name_id
                    ON metric_timings(name, id);
                """
            )
            conn.commit()

    def increment(self, name: str, value: int = 1) -> None:
        amount = int(value)
        with self._lock:
            if self.storage_path is None:
                self._counters[name] = self._counters.get(name, 0) + amount
                return
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO metric_counters(name,value) VALUES(?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",
                    (str(name), amount),
                )
                conn.commit()

    def set_gauge(self, name: str, value: float) -> None:
        numeric = float(value)
        with self._lock:
            if self.storage_path is None:
                self._gauges[name] = numeric
                return
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO metric_gauges(name,value) VALUES(?,?) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                    (str(name), numeric),
                )
                conn.commit()

    def observe_ms(self, name: str, value_ms: float) -> None:
        value = float(value_ms)
        with self._lock:
            if self.storage_path is None:
                values = self._timings.setdefault(name, [])
                values.append(value)
                if len(values) > 5000:
                    del values[:-5000]
                return
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO metric_timings(name,value_ms,created_at) VALUES(?,?,?)",
                    (str(name), value, time.time()),
                )
                # Keep only the newest 5000 observations per metric.
                conn.execute(
                    "DELETE FROM metric_timings WHERE name=? AND id NOT IN "
                    "(SELECT id FROM metric_timings WHERE name=? ORDER BY id DESC LIMIT 5000)",
                    (str(name), str(name)),
                )
                conn.commit()

    def _read_persistent(self) -> tuple[dict[str, int], dict[str, float], dict[str, list[float]]]:
        with self._connect() as conn:
            counters = {
                str(row[0]): int(row[1])
                for row in conn.execute("SELECT name,value FROM metric_counters")
            }
            gauges = {
                str(row[0]): float(row[1])
                for row in conn.execute("SELECT name,value FROM metric_gauges")
            }
            timings: dict[str, list[float]] = {}
            for row in conn.execute(
                "SELECT name,value_ms FROM metric_timings ORDER BY id"
            ):
                timings.setdefault(str(row[0]), []).append(float(row[1]))
        return counters, gauges, timings

    def snapshot(self) -> MetricSnapshot:
        with self._lock:
            if self.storage_path is None:
                counters = dict(self._counters)
                gauges = dict(self._gauges)
                timings = {name: list(values) for name, values in self._timings.items()}
            else:
                counters, gauges, timings = self._read_persistent()

            averages: dict[str, float] = {}
            percentiles: dict[str, dict[str, float]] = {}
            for name, vals in timings.items():
                if not vals:
                    continue
                ordered = sorted(vals)
                averages[name] = round(sum(ordered) / len(ordered), 3)

                def pct(fraction: float) -> float:
                    index = min(
                        len(ordered) - 1,
                        max(0, int(round((len(ordered) - 1) * fraction))),
                    )
                    return round(ordered[index], 3)

                percentiles[name] = {
                    "p50": pct(0.50),
                    "p90": pct(0.90),
                    "p95": pct(0.95),
                    "p99": pct(0.99),
                }
            return MetricSnapshot(
                counters,
                gauges,
                averages,
                percentiles,
                time.time(),
            )

    def prometheus(self) -> str:
        return self.to_prometheus()

    def to_prometheus(self) -> str:
        snapshot = self.snapshot()
        lines: list[str] = []
        for raw_name, count in snapshot.counters.items():
            if raw_name.startswith("http_requests_total:"):
                _, method, endpoint = raw_name.split(":", 2)
                method = re.sub(r"[^A-Za-z0-9_]", "_", method)
                endpoint = endpoint.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(
                    f'aivf_http_requests_total{{method="{method}",endpoint="{endpoint}"}} {int(count)}'
                )
            else:
                metric = re.sub(r"[^A-Za-z0-9_:]", "_", raw_name)
                lines.append(f"aivf_{metric} {int(count)}")
        for raw_name, value in snapshot.gauges.items():
            metric = re.sub(r"[^A-Za-z0-9_:]", "_", raw_name)
            lines.append(f"aivf_{metric} {value}")
        for raw_name, average_ms in snapshot.timings_ms.items():
            metric = re.sub(r"[^A-Za-z0-9_:]", "_", raw_name)
            quantiles = snapshot.timing_percentiles_ms.get(raw_name, {})
            for percentile, value in quantiles.items():
                lines.append(
                    f'aivf_{metric}_milliseconds{{quantile="{percentile}"}} {value}'
                )
        return "\n".join(lines) + ("\n" if lines else "")

    def to_json(self) -> str:
        s = self.snapshot()
        return json.dumps(
            {
                "counters": s.counters,
                "gauges": s.gauges,
                "timings_ms_avg": s.timings_ms,
                "timing_percentiles_ms": s.timing_percentiles_ms,
                "generated_at": s.generated_at,
            },
            sort_keys=True,
            indent=2,
        )


@contextmanager
def timed(registry: MetricsRegistry, name: str) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    finally:
        registry.observe_ms(name, (time.perf_counter() - started) * 1000.0)


def _default_metrics_path() -> Path | None:
    state_dir = os.environ.get("AIVF_STATE_DIR", "").strip()
    return (Path(state_dir) / "metrics.db").resolve() if state_dir else None


GLOBAL_METRICS = MetricsRegistry(_default_metrics_path())

__all__ = ["GLOBAL_METRICS", "MetricSnapshot", "MetricsRegistry", "timed"]
