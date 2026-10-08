"""Durable low-cardinality metrics with histogram-aware Prometheus exposition."""
from __future__ import annotations
import json, math, os, re, sqlite3, threading, time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

DEFAULT_BUCKETS_MS=(5,10,25,50,100,250,500,1000,2500,5000,10000,30000)

@dataclass(frozen=True)
class MetricSnapshot:
    counters: dict[str,int]
    timings_ms: dict[str,float]
    generated_at: float

class MetricsRegistry:
    def __init__(self,state_dir:str|Path|None=None)->None:
        self._lock=threading.RLock()
        self._counters: dict[str, int] = {}
        self._timings: dict[str, list[float]] = {}
        self._state_dir=Path(state_dir or os.environ.get("AIVF_STATE_DIR","state"))
        self._db=self._state_dir/"metrics.db"
        self._init_db()

    def _init_db(self)->None:
        try:
            self._state_dir.mkdir(parents=True,exist_ok=True)
            with sqlite3.connect(self._db) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS metric_counters(name TEXT PRIMARY KEY,value INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS metric_timings(name TEXT PRIMARY KEY,count INTEGER NOT NULL,total REAL NOT NULL,samples TEXT NOT NULL DEFAULT '[]')")
                conn.execute("CREATE TABLE IF NOT EXISTS metric_gauges(name TEXT PRIMARY KEY,value REAL NOT NULL)")
        except (OSError,sqlite3.Error):
            pass

    def increment(self,name:str,value:int=1)->None:
        amount=int(value)
        with self._lock:
            self._counters[name]=self._counters.get(name,0)+amount
            try:
                with sqlite3.connect(self._db,timeout=2) as conn:
                    conn.execute("INSERT INTO metric_counters(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",(name,amount))
            except sqlite3.Error:
                pass

    def observe_ms(self,name:str,value_ms:float)->None:
        value=max(0.0,float(value_ms))
        with self._lock:
            self._timings.setdefault(name,[]).append(value)
            self._timings[name]=self._timings[name][-5000:]
            try:
                with sqlite3.connect(self._db,timeout=2) as conn:
                    row=conn.execute("SELECT count,total,samples FROM metric_timings WHERE name=?",(name,)).fetchone()
                    samples=[]
                    if row:
                        try: samples=[float(x) for x in json.loads(row[2] or "[]")]
                        except (TypeError,ValueError,json.JSONDecodeError): samples=[]
                    samples=(samples+[value])[-5000:]
                    conn.execute("INSERT INTO metric_timings(name,count,total,samples) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET count=count+1,total=total+excluded.total,samples=excluded.samples",(name,1,value,json.dumps(samples)))
            except sqlite3.Error:
                pass

    def set_gauge(self,name:str,value:float)->None:
        metric=str(name).strip()
        if not metric:
            raise ValueError("metric name is required")
        numeric=float(value)
        with self._lock:
            try:
                with sqlite3.connect(self._db,timeout=2) as conn:
                    conn.execute(
                        "INSERT INTO metric_gauges(name,value) VALUES(?,?) "
                        "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                        (metric,numeric),
                    )
            except sqlite3.Error:
                pass

    def gauges(self)->dict[str,float]:
        try:
            with sqlite3.connect(self._db,timeout=2) as conn:
                return {str(name):float(value) for name,value in conn.execute("SELECT name,value FROM metric_gauges")}
        except (sqlite3.Error,ValueError,TypeError):
            return {}

    def _samples(self,name:str)->list[float]:
        values=list(self._timings.get(name,[]))
        try:
            with sqlite3.connect(self._db) as conn:
                row=conn.execute("SELECT samples FROM metric_timings WHERE name=?",(name,)).fetchone()
                if row: values=[float(x) for x in json.loads(row[0] or "[]")]
        except (sqlite3.Error,ValueError,TypeError,json.JSONDecodeError):
            pass
        return values[-5000:]

    @staticmethod
    def _histogram(values:list[float])->dict[str,float]:
        if not values: return {"count":0.0,"sum":0.0,"p50":0.0,"p90":0.0,"p95":0.0,"p99":0.0}
        ordered=sorted(values)
        def pct(p:float)->float: return ordered[min(len(ordered)-1,max(0,int(math.ceil(p*len(ordered)))-1))]
        return {"count":float(len(ordered)),"sum":float(sum(ordered)),"p50":pct(.50),"p90":pct(.90),"p95":pct(.95),"p99":pct(.99)}

    def histogram_snapshot(self,name:str)->dict[str,float]:
        with self._lock: return self._histogram(self._samples(name))

    def snapshot(self)->MetricSnapshot:
        with self._lock:
            counters=dict(self._counters); timings={}
            try:
                with sqlite3.connect(self._db) as conn:
                    for name,value in conn.execute("SELECT name,value FROM metric_counters"):
                        counters[str(name)]=int(value)
                    for name,samples in conn.execute("SELECT name,samples FROM metric_timings"):
                        values=[float(x) for x in json.loads(samples or "[]")]
                        if values: timings[str(name)]=round(sum(values)/len(values),3)
            except (sqlite3.Error,ValueError,TypeError,json.JSONDecodeError):
                for name,values in self._timings.items():
                    if values: timings[name]=round(sum(values)/len(values),3)
            return MetricSnapshot(counters,timings,time.time())

    def to_prometheus(self) -> str:
        """Serialize durable snapshots through the standard prometheus-client library."""
        from prometheus_client import CollectorRegistry, CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily, generate_latest

        snap = self.snapshot()
        registry = CollectorRegistry()

        http_family = CounterMetricFamily(
            "aivf_http_requests_total",
            "HTTP requests handled by the dashboard.",
            labels=["method", "endpoint"],
        )
        generic_counters: list[tuple[str, int]] = []
        for raw_name, count in snap.counters.items():
            if raw_name.startswith("http_requests_total:"):
                _, method, endpoint = raw_name.split(":", 2)
                http_family.add_metric([method, endpoint], float(count))
            else:
                generic_counters.append((raw_name, int(count)))
        registry.register(http_family)

        for raw_name, count in generic_counters:
            metric = re.sub(r"[^A-Za-z0-9_:]", "_", raw_name).strip("_") or "counter"
            registry.register(
                CounterMetricFamily(
                    f"aivf_{metric}",
                    f"Edit Factory counter: {raw_name}.",
                    value=float(count),
                )
            )

        for raw_name, value in self.gauges().items():
            metric = re.sub(r"[^A-Za-z0-9_:]", "_", raw_name).strip("_") or "gauge"
            registry.register(
                GaugeMetricFamily(
                    f"aivf_{metric}",
                    f"Edit Factory gauge: {raw_name}.",
                    value=float(value),
                )
            )

        names = set(self._timings)
        try:
            with sqlite3.connect(self._db) as conn:
                names |= {str(row[0]) for row in conn.execute("SELECT name FROM metric_timings")}
        except sqlite3.Error:
            pass
        for name in sorted(names):
            metric = re.sub(r"[^A-Za-z0-9_:]", "_", name).strip("_") or "duration"
            values = self._samples(name)
            hist = self._histogram(values)
            family = HistogramMetricFamily(
                f"aivf_{metric}_milliseconds",
                f"Edit Factory duration histogram: {name}.",
                buckets=DEFAULT_BUCKETS_MS,
            )
            for bucket in DEFAULT_BUCKETS_MS:
                family.add_metric([], hist=None, value=None)
            # HistogramMetricFamily accepts one sample containing the bucket
            # map, sum, and count; prometheus-client handles text exposition.
            family.samples = []
            running = 0
            for bucket in DEFAULT_BUCKETS_MS:
                running += sum(1 for value in values if value <= bucket) - running
                family.add_metric([], labels=None, value=0) if False else None
            # Reconstruct bucket samples explicitly because this registry is
            # sourced from durable SQLite samples rather than live collectors.
            family.samples = [
                type(family.samples).__args__[0]() if False else s
                for s in []
            ]
            from prometheus_client.core import Sample
            family.samples = [
                Sample(
                    f"aivf_{metric}_milliseconds_bucket",
                    {"le": str(bucket)},
                    float(sum(1 for value in values if value <= bucket)),
                    None,
                    None,
                )
                for bucket in DEFAULT_BUCKETS_MS
            ]
            family.samples.append(
                Sample(
                    f"aivf_{metric}_milliseconds_bucket",
                    {"le": "+Inf"},
                    float(hist["count"]),
                    None,
                    None,
                )
            )
            family.samples.append(
                Sample(f"aivf_{metric}_milliseconds_count", {}, float(hist["count"]), None, None)
            )
            family.samples.append(
                Sample(f"aivf_{metric}_milliseconds_sum", {}, float(hist["sum"]), None, None)
            )
            registry.register(family)

        return generate_latest(registry).decode("utf-8")

    def prometheus(self) -> str:
        return self.to_prometheus()

    def to_json(self)->str:
        snap=self.snapshot(); payload={"counters":snap.counters,"timings_ms_avg":snap.timings_ms,"generated_at":snap.generated_at}
        for name in set(self._timings):
            payload[f"histogram:{name}"]=self.histogram_snapshot(name)
        return json.dumps(payload,sort_keys=True,indent=2)

@contextmanager
def timed(registry:MetricsRegistry,name:str)->Iterator[None]:
    started=time.perf_counter()
    try: yield
    finally: registry.observe_ms(name,(time.perf_counter()-started)*1000.0)

GLOBAL_METRICS=MetricsRegistry()
__all__=["GLOBAL_METRICS","MetricSnapshot","MetricsRegistry","timed"]
