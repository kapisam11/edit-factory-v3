"""Probe-based compatibility matrix for unusual media inputs."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable,Any
from .media_health import analyze_media

def classify_media(path:str|Path)->dict[str,Any]:
    try:
        report=analyze_media(path,deep=False,max_duration=86400.0)
        summary=report["summary"]
        return {"path":str(path),"ok":bool(report["ok"]),"duration":summary.get("duration"),"width":summary.get("width"),"height":summary.get("height"),"codec":summary.get("video_codec"),"audio_codec":summary.get("audio_codec"),"fps":summary.get("fps"),"pixel_format":summary.get("pixel_format"),"error":None}
    except Exception as exc:
        return {"path":str(path),"ok":False,"error":str(exc)}

def run_matrix(paths:Iterable[str|Path])->dict[str,Any]:
    items=[classify_media(path) for path in paths]
    return {"count":len(items),"passed":sum(bool(item["ok"]) for item in items),"failed":sum(not bool(item["ok"]) for item in items),"items":items}

__all__=["classify_media","run_matrix"]
