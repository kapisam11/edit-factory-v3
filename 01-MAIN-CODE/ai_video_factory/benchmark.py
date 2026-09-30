"""Small repeatable media benchmark runner."""
from __future__ import annotations
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .media_health import probe_media
from .production_guardrails import sha256_file
from .render_engine import validate_media_output

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

def benchmark_encode(path:str|Path, *, encoder:str="libx264", duration_seconds:float=3.0)->dict[str,Any]:
    source=Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    binary=shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("ffmpeg is required")
    opts: list[str] = []
    if encoder in {"h264_nvenc", "hevc_nvenc"}:
        opts=["-preset","p5","-rc","vbr_hq","-b:v","6M"]
    elif encoder == "h264_amf":
        opts=["-quality","speed","-b:v","6M"]
    elif encoder == "h264_qsv":
        opts=["-preset","veryfast","-b:v","6M"]
    elif encoder not in {"libx264"}:
        raise ValueError(f"unsupported benchmark encoder: {encoder}")
    with tempfile.TemporaryDirectory(prefix="aivf-benchmark-") as tmp:
        output=Path(tmp)/"encoded.mp4"
        command=[binary,"-hide_banner","-loglevel","error","-y","-i",str(source),"-t",str(float(duration_seconds)),"-an","-c:v",encoder,*opts,str(output)]
        started=time.perf_counter()
        result=subprocess.run(command,capture_output=True,text=True,timeout=max(60,int(duration_seconds)*120),check=False)
        elapsed=time.perf_counter()-started
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout)[-3000:])
        validate_media_output(str(output),require_video=True,require_audio=False)
        size=output.stat().st_size
        return {
            "encoder":encoder,
            "source":str(source),
            "duration_seconds":round(float(duration_seconds),3),
            "encode_seconds":round(elapsed,6),
            "output_size_bytes":size,
            "realtime_factor":round(float(duration_seconds)/max(elapsed,1e-9),4),
        }


def compare_benchmarks(before:dict[str,Any],after:dict[str,Any])->dict[str,Any]:
    def ratio(key:str)->float|None:
        a=float(before.get(key) or 0); b=float(after.get(key) or 0)
        return None if a<=0 else round((b-a)/a,4)
    return {"before":before,"after":after,"probe_time_delta":ratio("probe_seconds"),"hash_time_delta":ratio("sha256_seconds")}

def benchmark_many(paths:list[str|Path], repeats:int=1)->dict[str,Any]:
    results=[benchmark_media(path,repeats=repeats) for path in paths]
    return {
        "count": len(results),
        "results": results,
        "total_bytes": sum(int(item["size_bytes"]) for item in results),
        "average_probe_seconds": round(sum(float(item["probe_seconds"]) for item in results)/max(1,len(results)),6),
    }


def write_benchmark(path:str|Path,data:dict[str,Any])->str:
    target=Path(path); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,indent=2,sort_keys=True),encoding="utf-8")
    return str(target)

__all__=["benchmark_encode","benchmark_many","benchmark_media","compare_benchmarks","write_benchmark"]
