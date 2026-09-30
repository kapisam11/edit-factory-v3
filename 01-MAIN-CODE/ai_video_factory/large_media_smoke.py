"""Opt-in large-file smoke helpers without forcing 10+ GB CI downloads."""
from __future__ import annotations
import os
from pathlib import Path
from .production_guardrails import free_disk_bytes, sha256_file
from .render_engine import validate_media_output, run_ffmpeg

def create_sparse_file(path:str|Path,size_gb:float=10.0)->str:
    target=Path(path)
    if size_gb<=0: raise ValueError("size_gb must be positive")
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open("wb") as handle:
        handle.truncate(int(float(size_gb)*1024**3))
    return str(target)

def create_video_fixture(path:str|Path, *, duration_seconds:float=30.0, size="1920x1080", bitrate="12M")->str:
    target=Path(path)
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    target.parent.mkdir(parents=True,exist_ok=True)
    run_ffmpeg([
        "ffmpeg","-hide_banner","-loglevel","error","-y",
        "-f","lavfi","-i",f"testsrc2=size={size}:rate=30",
        "-f","lavfi","-i","anullsrc=r=48000:cl=stereo",
        "-t",str(float(duration_seconds)),"-shortest",
        "-c:v","libx264","-b:v",str(bitrate),"-c:a","aac","-b:a","192k",
        str(target),
    ], timeout=1800)
    validate_media_output(str(target), require_video=True, require_audio=True)
    return str(target)


def smoke_large_file(path:str|Path)->dict[str,int|str]:
    target=Path(path)
    if not target.is_file(): raise FileNotFoundError(target)
    digest=sha256_file(target)
    return {"path":str(target),"size_bytes":target.stat().st_size,"free_bytes":free_disk_bytes(target.parent),"sha256":digest}

def enabled()->bool:
    return os.environ.get("AIVF_RUN_LARGE_MEDIA","0")=="1"

__all__=["create_sparse_file","create_video_fixture","enabled","smoke_large_file"]
