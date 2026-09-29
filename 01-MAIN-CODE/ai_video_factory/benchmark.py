"""Small repeatable media benchmark runner."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Callable

from .media_health import probe_media
from .production_guardrails import sha256_file

def _timed(fn:Callable[[],Any],repeats:int=1)->tuple[Any,float]:
    start=time.perf_counter(); result=None
    for _ in range(max(1,int(repeats))): result=fn()
    elapsed=time.perf_counter()-start
    return result,elapsed/max(1,int(repeats))

def benchmark_media(path:str|Path,repeats:int=1)->dict[str,Any]:
    probe,probe_s=_timed(lambda:probe_media(path),repeats)
    _,hash_s=_timed(lambda:sha256_file(path),1)
    size=Path(path).stat().st_size
    return {
        "file":str(path),
        "size_bytes":size,
        "probe_seconds":round(probe_s,6),
        "sha256_seconds":round(hash_s,6),
        "probe":probe,
        "throughput_mb_s":round(size/(1024*1024)/max(hash_s,1e-9),3),
    }

def compare_benchmarks(before:dict[str,Any],after:dict[str,Any])->dict[str,Any]:
    def ratio(key:str)->float|None:
        a=float(before.get(key) or 0); b=float(after.get(key) or 0)
        return None if a<=0 else round((b-a)/a,4)
    return {"before":before,"after":after,"probe_time_delta":ratio("probe_seconds"),"hash_time_delta":ratio("sha256_seconds")}

def write_benchmark(path:str|Path,data:dict[str,Any])->str:
    target=Path(path); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,indent=2,sort_keys=True),encoding="utf-8")
    return str(target)

__all__=["benchmark_media","compare_benchmarks","write_benchmark"]
