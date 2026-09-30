"""Dependency-free counters and timing metrics."""
from __future__ import annotations
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

@dataclass(frozen=True)
class MetricSnapshot:
    counters: dict[str,int]
    timings_ms: dict[str,float]
    generated_at: float

class MetricsRegistry:
    def __init__(self)->None:
        self._lock=threading.RLock(); self._counters: dict[str, int] = {}; self._timings: dict[str, list[float]] = {}

    def increment(self,name:str,value:int=1)->None:
        with self._lock: self._counters[name]=self._counters.get(name,0)+int(value)

    def observe_ms(self,name:str,value_ms:float)->None:
        with self._lock:
            self._timings.setdefault(name,[]).append(float(value_ms))
            if len(self._timings[name])>5000: self._timings[name]=self._timings[name][-5000:]

    def snapshot(self)->MetricSnapshot:
        with self._lock:
            averages={name:round(sum(vals)/len(vals),3) for name,vals in self._timings.items() if vals}
            return MetricSnapshot(dict(self._counters),averages,time.time())

    def percentile_ms(self, name:str, percentile:float=0.95)->float:
        target=max(0.0,min(1.0,float(percentile)))
        with self._lock:
            values=sorted(self._timings.get(name, []))
        if not values:
            return 0.0
        index=min(len(values)-1,max(0,int(round(target*(len(values)-1)))))
        return round(values[index],3)

    def prometheus(self)->str:
        snapshot=self.snapshot()
        lines=[]
        for counter_name,counter_value in snapshot.counters.items():
            metric=counter_name.replace(":","_").replace("-","_")
            lines.append(f'aivf_{metric} {int(counter_value)}')
        for timing_name,timing_value in snapshot.timings_ms.items():
            metric=timing_name.replace(":","_").replace("-","_")
            lines.append(f'aivf_{metric}_avg_ms {float(timing_value)}')
            lines.append(f'aivf_{metric}_p95_ms {self.percentile_ms(timing_name,0.95)}')
        return "\n".join(lines)+"\n"

    def to_json(self)->str:
        s=self.snapshot()
        return json.dumps({"counters":s.counters,"timings_ms_avg":s.timings_ms,"generated_at":s.generated_at},sort_keys=True,indent=2)

@contextmanager
def timed(registry: MetricsRegistry,name:str)->Iterator[None]:
    started=time.perf_counter()
    try: yield
    finally: registry.observe_ms(name,(time.perf_counter()-started)*1000.0)

GLOBAL_METRICS=MetricsRegistry()
__all__=["GLOBAL_METRICS","MetricSnapshot","MetricsRegistry","timed"]
