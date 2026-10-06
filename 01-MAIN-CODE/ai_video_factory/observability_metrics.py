"""Dependency-free counters and timing metrics."""
from __future__ import annotations
import json
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

@dataclass(frozen=True)
class MetricSnapshot:
    counters: dict[str,int|float]
    timings_ms: dict[str,float]
    timing_percentiles_ms: dict[str,dict[str,float]]
    generated_at: float

class MetricsRegistry:
    def __init__(self)->None:
        self._lock=threading.RLock(); self._counters: dict[str, int] = {}; self._gauges: dict[str, float] = {}; self._timings: dict[str, list[float]] = {}

    def increment(self,name:str,value:int=1)->None:
        with self._lock: self._counters[name]=self._counters.get(name,0)+int(value)

    def set_gauge(self,name:str,value:float)->None:
        with self._lock:
            self._gauges[name]=float(value)

    def observe_ms(self,name:str,value_ms:float)->None:
        with self._lock:
            self._timings.setdefault(name,[]).append(float(value_ms))
            if len(self._timings[name])>5000: self._timings[name]=self._timings[name][-5000:]

    def to_prometheus(self) -> str:
        """Export low-cardinality counters/timings in Prometheus text format."""
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
            elif raw_name.startswith("gauge:"):
                metric = re.sub(r"[^A-Za-z0-9_:]", "_", raw_name[6:])
                lines.append(f"aivf_{metric} {float(count)}")
            else:
                metric = re.sub(r"[^A-Za-z0-9_:]", "_", raw_name)
                lines.append(f"aivf_{metric} {int(count)}")
        for raw_name, timing_ms in snapshot.timings_ms.items():
            metric = re.sub(r"[^A-Za-z0-9_:]", "_", raw_name)
            lines.append(f"aivf_{metric}_milliseconds_avg {timing_ms}")
            percentiles = snapshot.timing_percentiles_ms.get(raw_name, {})
            for percentile, value in percentiles.items():
                lines.append(f'aivf_{metric}_milliseconds{{quantile="{percentile}"}} {value}')
        return "\n".join(lines) + ("\n" if lines else "")

    def snapshot(self)->MetricSnapshot:
        with self._lock:
            averages = {}
            percentiles = {}
            for name, vals in self._timings.items():
                if not vals:
                    continue
                ordered = sorted(vals)
                averages[name] = round(sum(ordered) / len(ordered), 3)
                def pct(fraction: float) -> float:
                    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
                    return round(ordered[index], 3)
                percentiles[name] = {
                    "p50": pct(0.50),
                    "p90": pct(0.90),
                    "p95": pct(0.95),
                    "p99": pct(0.99),
                }
            counters: dict[str, int | float] = dict(self._counters)
            for name, value in self._gauges.items():
                counters[f"gauge:{name}"] = value
            return MetricSnapshot(counters, averages, percentiles, time.time())

    def prometheus(self) -> str:
        return self.to_prometheus()

    def to_json(self)->str:
        s=self.snapshot()
        return json.dumps({"counters":s.counters,"timings_ms_avg":s.timings_ms,"timing_percentiles_ms":s.timing_percentiles_ms,"generated_at":s.generated_at},sort_keys=True,indent=2)

@contextmanager
def timed(registry: MetricsRegistry,name:str)->Iterator[None]:
    started=time.perf_counter()
    try: yield
    finally: registry.observe_ms(name,(time.perf_counter()-started)*1000.0)

GLOBAL_METRICS=MetricsRegistry()
__all__=["GLOBAL_METRICS","MetricSnapshot","MetricsRegistry","timed"]
