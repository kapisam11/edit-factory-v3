"""Media health analysis and render-contract checks."""
from __future__ import annotations
from .v3_exceptions import V3ValidationError
import json, math, os, re
from pathlib import Path
from fractions import Fraction
from typing import Any, Mapping
from .production_guardrails import run_tool

class MediaHealthError(V3ValidationError):
    pass

def probe_media(path: str | Path) -> dict[str, Any]:
    source=Path(path)
    if not source.is_file() or source.stat().st_size <= 0:
        raise MediaHealthError(f"media file is missing or empty: {source}")
    r=run_tool(["ffprobe","-v","error","-show_entries","format=duration,size,format_name,bit_rate","-show_streams","-of","json",str(source)],timeout=60)
    if r.returncode != 0:
        raise MediaHealthError(f"ffprobe failed: {r.stderr_tail}")
    try:
        payload=json.loads(r.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaHealthError("ffprobe returned invalid JSON") from exc
    if not isinstance(payload,dict) or not isinstance(payload.get("streams") or [],list):
        raise MediaHealthError("invalid ffprobe payload")
    return payload

def stream_summary(probe: Mapping[str,Any]) -> dict[str,Any]:
    streams=list(probe.get("streams") or [])
    video=next((s for s in streams if s.get("codec_type")=="video"),None)
    audio=next((s for s in streams if s.get("codec_type")=="audio"),None)
    fmt=probe.get("format") or {}
    return {
        "duration":float(fmt.get("duration") or 0),
        "size":int(fmt.get("size") or 0),
        "bit_rate":int(fmt.get("bit_rate") or 0),
        "format_name":fmt.get("format_name"),
        "has_video":video is not None,
        "has_audio":audio is not None,
        "width":int((video or {}).get("width") or 0),
        "height":int((video or {}).get("height") or 0),
        "video_codec":(video or {}).get("codec_name"),
        "audio_codec":(audio or {}).get("codec_name") if audio else None,
        "fps":(video or {}).get("avg_frame_rate") or (video or {}).get("r_frame_rate") or "0/0",
        "avg_frame_rate": (video or {}).get("avg_frame_rate") or "0/0",
        "r_frame_rate": (video or {}).get("r_frame_rate") or "0/0",
        "video_duration": float((video or {}).get("duration") or fmt.get("duration") or 0),
        "audio_duration": float((audio or {}).get("duration") or fmt.get("duration") or 0) if audio else None,
        "pixel_format":(video or {}).get("pix_fmt"),
        "rotation":((video or {}).get("tags") or {}).get("rotate"),
    }

def _stream_fps(value: str) -> float:
    try:
        return float(Fraction(str(value or "0/0")))
    except (ValueError, ZeroDivisionError):
        return 0.0


def detect_vfr(summary: Mapping[str, Any], *, tolerance: float = 0.5) -> bool:
    avg = _stream_fps(str(summary.get("avg_frame_rate", "0/0")))
    nominal = _stream_fps(str(summary.get("r_frame_rate", "0/0")))
    return avg > 0 and nominal > 0 and abs(avg - nominal) > float(tolerance)


def av_sync_delta(summary: Mapping[str, Any]) -> float | None:
    if not summary.get("has_audio"):
        return None
    video_duration = float(summary.get("video_duration") or 0.0)
    audio_duration = float(summary.get("audio_duration") or 0.0)
    if video_duration <= 0 or audio_duration <= 0:
        return None
    return round(audio_duration - video_duration, 4)


def silence_ratio(silence_segments: list[Mapping[str, float]], duration: float) -> float:
    total = sum(max(0.0, float(item.get("duration") or 0.0)) for item in silence_segments)
    return round(min(1.0, total / max(0.001, float(duration))), 4)


def parse_fps(rate:str)->float:
    m=re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*",str(rate or ""))
    if not m:
        return 0.0
    numerator,denominator=float(m.group(1)),float(m.group(2))
    return numerator/denominator if denominator else 0.0

def aspect_ratio(width:int,height:int)->float:
    return float(width)/float(height) if int(width)>0 and int(height)>0 else 0.0

def duration_bounds(duration:float,minimum:float=0.25,maximum:float=1800.0)->bool:
    return math.isfinite(float(duration)) and float(minimum)<=float(duration)<=float(maximum)

def validate_media_contract(path:str|Path,*,require_video:bool=True,require_audio:bool=False,min_duration:float=0.25,max_duration:float=1800.0,min_width:int=0,min_height:int=0,min_fps:float=0.0)->dict[str,Any]:
    summary=stream_summary(probe_media(path))
    if require_video and not summary["has_video"]:
        raise MediaHealthError("media has no video stream")
    if require_audio and not summary["has_audio"]:
        raise MediaHealthError("media has no audio stream")
    if not duration_bounds(summary["duration"],min_duration,max_duration):
        raise MediaHealthError(f"invalid media duration: {summary['duration']}")
    if summary["width"]<int(min_width) or summary["height"]<int(min_height):
        raise MediaHealthError("media dimensions are below the contract")
    if parse_fps(str(summary["fps"]))<float(min_fps):
        raise MediaHealthError("media FPS is below the required minimum")
    return summary

def _events(text:str,start_key:str,end_key:str)->list[dict[str,float]]:
    starts=[float(v) for v in re.findall(rf"{re.escape(start_key)}\s*:\s*([0-9.]+)",text)]
    ends=[float(v) for v in re.findall(rf"{re.escape(end_key)}\s*:\s*([0-9.]+)",text)]
    return [{"start":a,"end":b,"duration":max(0.0,b-a)} for a,b in zip(starts,ends)]

def detect_black_frames(path:str|Path,min_duration:float=0.5)->list[dict[str,float]]:
    r=run_tool(["ffmpeg","-hide_banner","-i",str(path),"-vf",f"blackdetect=d={float(min_duration):.3f}:pic_th=0.98","-an","-f","null","-"],timeout=600,stderr_limit=1024*1024)
    if r.returncode!=0:
        raise MediaHealthError(f"black-frame analysis failed: {r.stderr_tail}")
    return _events(r.stderr_tail,"black_start","black_end")

def detect_freeze_frames(path:str|Path,min_duration:float=1.0)->list[dict[str,float]]:
    r=run_tool(["ffmpeg","-hide_banner","-i",str(path),"-vf",f"freezedetect=n=-60dB:d={float(min_duration):.3f}","-an","-f","null","-"],timeout=600,stderr_limit=1024*1024)
    if r.returncode!=0:
        raise MediaHealthError(f"freeze-frame analysis failed: {r.stderr_tail}")
    return _events(r.stderr_tail,"freeze_start","freeze_end")

def detect_silence(path:str|Path,threshold_db:float=-50.0,min_duration:float=0.5)->list[dict[str,float]]:
    r=run_tool(["ffmpeg","-hide_banner","-i",str(path),"-af",f"silencedetect=noise={float(threshold_db):.1f}dB:d={float(min_duration):.3f}","-f","null","-"],timeout=600,stderr_limit=1024*1024)
    if r.returncode!=0:
        raise MediaHealthError(f"silence analysis failed: {r.stderr_tail}")
    pending=None
    out=[]
    for line in r.stderr_tail.splitlines():
        if "silence_start:" in line:
            try: pending=float(line.split("silence_start:",1)[1].strip().split()[0])
            except (IndexError,ValueError): pass
        elif "silence_end:" in line and pending is not None:
            try:
                end=float(line.split("silence_end:",1)[1].strip().split("|",1)[0].split()[0])
                out.append({"start":pending,"end":end,"duration":max(0.0,end-pending)})
                pending=None
            except (IndexError,ValueError): pass
    return out

def audio_loudness(path:str|Path)->dict[str,Any]:
    r=run_tool(["ffmpeg","-hide_banner","-i",str(path),"-af","loudnorm=print_format=json","-f","null","-"],timeout=600,stderr_limit=1024*1024)
    if r.returncode!=0:
        raise MediaHealthError(f"audio loudness analysis failed: {r.stderr_tail}")
    raw=r.stderr_tail
    start,end=raw.find("{"),raw.rfind("}")
    if start<0 or end<=start:
        raise MediaHealthError("loudnorm returned no JSON")
    try: payload=json.loads(raw[start:end+1])
    except json.JSONDecodeError as exc: raise MediaHealthError("loudnorm returned malformed JSON") from exc
    return {"integrated_lufs":payload.get("input_i"),"true_peak":payload.get("input_tp"),"loudness_range":payload.get("input_lra")}

def blur_score(path:str|Path, *, samples: int = 5)->float|None:
    try:
        import cv2
    except Exception:
        return None
    cap=cv2.VideoCapture(str(path))
    if not cap.isOpened(): return None
    try:
        frame_count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        indices=sorted({0, max(0, frame_count//4), max(0, frame_count//2), max(0, (3*frame_count)//4), max(0, frame_count-1)})
        indices=indices[:max(1, int(samples))]
        scores=[]
        for index in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok,frame=cap.read()
            if not ok:
                continue
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
            scores.append(float(cv2.Laplacian(gray,cv2.CV_64F).var()))
        if not scores:
            return None
        return round(min(1.0, (sum(scores)/len(scores))/500.0),4)
    finally: cap.release()

def media_quality_score(summary:Mapping[str,Any],black_count:int=0,freeze_count:int=0)->float:
    if not summary.get("has_video"): return 0.0
    score=1.0
    if summary.get("width",0)<720 or summary.get("height",0)<720: score-=0.15
    if parse_fps(str(summary.get("fps","0/0")))<23: score-=0.15
    score-=min(0.35,0.08*int(black_count))
    score-=min(0.35,0.10*int(freeze_count))
    return round(max(0.0,min(1.0,score)),4)

def media_fingerprint(path:str|Path, summary:Mapping[str,Any]|None=None)->dict[str,Any]:
    from .production_guardrails import sha256_file
    s=dict(summary) if summary is not None else stream_summary(probe_media(path))
    return {"sha256":sha256_file(path),"size":s["size"],"duration":round(s["duration"],3),"width":s["width"],"height":s["height"],"video_codec":s["video_codec"],"audio_codec":s["audio_codec"]}

def analyze_media(path:str|Path,deep:bool=False,*,max_duration:float=1800.0)->dict[str,Any]:
    s=validate_media_contract(path,require_video=True,max_duration=float(max_duration))
    report: dict[str, Any] = {"ok":True,"summary":s,"black_frames":[],"freeze_frames":[],"silence_segments":[],"audio":None,"blur_score":None,"defects":[],"warnings":[],
        "timing":{"av_sync_delta_seconds":av_sync_delta(s),"vfr":detect_vfr(s)}}
    if deep:
        report["black_frames"]=detect_black_frames(path)
        report["freeze_frames"]=detect_freeze_frames(path)
        if s["has_audio"]:
            report["silence_segments"]=detect_silence(path)
            report["audio"]=audio_loudness(path)
        report["blur_score"]=blur_score(path, samples=5)
    report["quality_score"]=media_quality_score(s,len(report["black_frames"]),len(report["freeze_frames"]))
    if report["timing"]["vfr"]:
        report["warnings"].append("source appears variable-frame-rate; downstream CFR normalization should be explicit")
    sync_delta = report["timing"].get("av_sync_delta_seconds")
    if sync_delta is not None and abs(float(sync_delta)) > 0.5:
        report["defects"].append(f"audio/video duration delta exceeds tolerance: {sync_delta:.3f}s")
    report["silence_ratio"] = silence_ratio(report["silence_segments"], s["duration"]) if report["silence_segments"] else 0.0
    report["fingerprint"]=media_fingerprint(path, s)
    if report["quality_score"] < 0.8:
        report["warnings"].append("technical media quality is below the preferred threshold")
    if report["silence_ratio"] > 0.35:
        report["warnings"].append("audio contains an unusually high silence ratio")
    if report["blur_score"] is not None and report["blur_score"] < 0.25:
        report["warnings"].append("sampled frames have low sharpness; visual review is recommended")
    if report["black_frames"]:
        report["defects"].append(f"detected {len(report['black_frames'])} black-frame event(s)")
    if report["freeze_frames"]:
        report["defects"].append(f"detected {len(report['freeze_frames'])} freeze-frame event(s)")
    if report["defects"]:
        report["ok"]=False
        report["warnings"].extend(report["defects"])
    return report

def analyze_source_media(path:str|Path, *, max_duration:float=86400.0, deep:bool=False)->dict[str,Any]:
    """Validate source footage with a separate long-duration contract."""
    return analyze_media(path, deep=deep, max_duration=max_duration)

def assert_render_quality(path:str|Path,target_seconds:float,require_audio:bool=False)->dict[str,Any]:
    s=validate_media_contract(path,require_video=True,require_audio=require_audio,min_duration=max(.25,float(target_seconds)-.5),max_duration=float(target_seconds)+.5)
    if abs(s["duration"]-float(target_seconds))>.08: raise MediaHealthError("final render duration is outside tolerance")
    return s
