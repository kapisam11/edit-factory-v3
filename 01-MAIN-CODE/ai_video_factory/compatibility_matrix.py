"""Probe-based compatibility matrix for unusual media inputs."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable,Any
from .media_health import analyze_media

def classify_media(path:str|Path)->dict[str,Any]:
    try:
        report=analyze_media(path,deep=False,max_duration=86400.0)
        summary=report["summary"]
        width=int(summary.get("width") or 0)
        height=int(summary.get("height") or 0)
        duration=float(summary.get("duration") or 0.0)
        pixel_format=str(summary.get("pixel_format") or "")
        codec=str(summary.get("video_codec") or "")
        flags={
            "positive_duration": duration > 0,
            "nonzero_dimensions": width > 0 and height > 0,
            "dimensions_not_oversized": width <= 7680 and height <= 7680,
            "pixel_format_known": bool(pixel_format),
            "codec_known": bool(codec),
            "duration_not_extreme": duration <= 86400.0,
        }
        ok=bool(report["ok"]) and all(flags.values())
        return {"path":str(path),"ok":ok,"duration":duration,"width":width,"height":height,"codec":codec,"audio_codec":summary.get("audio_codec"),"fps":summary.get("fps"),"pixel_format":pixel_format,"compatibility_flags":flags,"error":None if ok else "media compatibility contract failed"}
    except Exception as exc:
        return {"path":str(path),"ok":False,"error":str(exc)}

def run_matrix(paths:Iterable[str|Path])->dict[str,Any]:
    items=[classify_media(path) for path in paths]
    return {"count":len(items),"passed":sum(bool(item["ok"]) for item in items),"failed":sum(not bool(item["ok"]) for item in items),"items":items}

__all__=["classify_media","run_matrix"]
