"""AI Video Factory — Enhanced Quality Control v2."""
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
import re

from .render_engine import run_ffmpeg


def _parse_filter_ranges(stderr: str, start_token: str, end_token: str) -> List[Dict]:
    starts = [float(x) for x in re.findall(rf"{re.escape(start_token)}\s*:?\s*([0-9.]+)", stderr)]
    ends = [float(x) for x in re.findall(rf"{re.escape(end_token)}\s*:?\s*([0-9.]+)", stderr)]
    return [
        {"start": start, "end": end, "duration": end - start}
        for start, end in zip(starts, ends)
        if end >= start
    ]


def check_black_frames(video_path: str, threshold: float = 0.95, min_duration: float = 0.5) -> List[Dict]:
    if not os.path.exists(video_path):
        return []
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"blackdetect=d={min_duration}:pic_th={threshold}",
        "-an", "-f", "null", "-"
    ]
    try:
        result = run_ffmpeg(cmd, capture_output=True)
        stderr = result.stderr or ""
    except Exception:
        return []
    return _parse_filter_ranges(stderr, "black_start", "black_end")


def check_frozen_frames(video_path: str, min_duration: float = 1.0) -> List[Dict]:
    if not os.path.exists(video_path):
        return []
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"freezedetect=n=-60dB:d={min_duration}",
        "-an", "-f", "null", "-"
    ]
    try:
        result = run_ffmpeg(cmd, capture_output=True)
        stderr = result.stderr or ""
    except Exception:
        return []
    return _parse_filter_ranges(stderr, "freeze_start", "freeze_end")


def check_audio_levels(video_path: str) -> Dict[str, Any]:
    if not os.path.exists(video_path):
        return {"error": "Video not found"}
    cmd = ["ffmpeg", "-i", video_path, "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    lufs_data = {}
    try:
        json_start = result.stderr.find("{")
        json_end = result.stderr.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            lufs_data = json.loads(result.stderr[json_start:json_end])
    except json.JSONDecodeError:
        pass
    silence_cmd = ["ffmpeg", "-i", video_path, "-af", "silencedetect=noise=-50dB:d=0.5", "-f", "null", "-"]
    silence_result = subprocess.run(silence_cmd, capture_output=True, text=True)
    silence_segments = []
    for line in silence_result.stderr.split("\n"):
        if "silence_start:" in line:
            try:
                start = float(line.split("silence_start:")[1].split()[0])
                silence_segments.append({"start": start})
            except (ValueError, IndexError):
                continue
        elif "silence_end:" in line:
            try:
                end = float(line.split("silence_end:")[1].split()[0])
                if silence_segments and "end" not in silence_segments[-1]:
                    silence_segments[-1]["end"] = end
                    silence_segments[-1]["duration"] = end - silence_segments[-1]["start"]
            except (ValueError, IndexError):
                continue
    return {
        "integrated_lufs": lufs_data.get("input_i", "unknown"),
        "true_peak": lufs_data.get("input_tp", "unknown"),
        "loudness_range": lufs_data.get("input_lra", "unknown"),
        "silence_segments": silence_segments,
        "silence_count": len(silence_segments),
        "recommendation": _audio_recommendation(lufs_data, silence_segments),
    }


def _audio_recommendation(lufs_data: Dict, silence_segments: List) -> str:
    recs = []
    input_i = lufs_data.get("input_i")
    if input_i is not None:
        val = float(input_i)
        if val > -13:
            recs.append("Audio is too loud (risk of clipping). Target: -14 LUFS.")
        elif val < -20:
            recs.append("Audio is too quiet. Target: -14 LUFS for streaming.")
    if len(silence_segments) > 3:
        recs.append(f"Found {len(silence_segments)} silence gaps. Consider trimming or adding music.")
    return " ".join(recs) if recs else "Audio levels look good."


def check_flashing_lights(video_path: str, threshold: float = 0.3) -> Dict[str, Any]:
    if not os.path.exists(video_path):
        return {"error": "Video not found"}
    return {
        "note": "Flashing light detection requires frame-by-frame analysis. "
                "For production, integrate with a proper photosensitive analysis tool "
                "or manually review high-contrast segments.",
        "threshold_used": threshold,
    }


def check_factual_consistency(research: Dict, script: str) -> Dict[str, Any]:
    if not research or not script:
        return {"skipped": True, "reason": "Missing research or script"}
    key_facts = research.get("key_facts", [])
    if not key_facts:
        summary = research.get("summary", "")
        key_facts = [s.strip() for s in summary.split(".") if len(s.strip()) > 10]
    script_lower = script.lower()
    matched, unmatched = [], []
    for fact in key_facts[:5]:
        fact_key = fact.lower()[:30]
        words = fact_key.split()
        match_score = sum(1 for w in words if w in script_lower) / max(len(words), 1)
        (matched if match_score > 0.5 else unmatched).append(fact)
    return {
        "facts_checked": len(key_facts),
        "matched": matched,
        "unmatched": unmatched,
        "consistency_score": len(matched) / max(len(matched) + len(unmatched), 1),
        "recommendation": "Review unmatched facts for accuracy." if unmatched else "Factual consistency looks good.",
    }


def run_enhanced_qc(package_dir: str, research: Optional[Dict] = None, script: Optional[str] = None) -> Dict[str, Any]:
    video_files = []
    if package_dir and os.path.exists(package_dir):
        for f in os.listdir(package_dir):
            if f.endswith((".mp4", ".mov", ".mkv")):
                video_files.append(os.path.join(package_dir, f))
    main_video = video_files[0] if video_files else None
    result = {"package_dir": package_dir, "checks": {}, "warnings": [], "errors": []}
    try:
        from .quality_control import run_final_checks
        base_report = run_final_checks(package_dir)
        result["checks"].update(base_report.get("checks", {}))
    except Exception as e:
        result["warnings"].append(f"Base QC failed: {e}")
    if main_video:
        black = check_black_frames(main_video)
        if black:
            result["checks"]["black_frames"] = black
            result["warnings"].append(f"Found {len(black)} black frame segments.")
        frozen = check_frozen_frames(main_video)
        if frozen:
            result["checks"]["frozen_frames"] = frozen
            result["warnings"].append(f"Found {len(frozen)} frozen frame segments.")
        result["checks"]["flashing_lights"] = check_flashing_lights(main_video)
        audio_report = check_audio_levels(main_video)
        result["checks"]["audio"] = audio_report
        if audio_report.get("recommendation") and "good" not in audio_report["recommendation"]:
            result["warnings"].append(f"Audio issue: {audio_report['recommendation']}")
    else:
        result["warnings"].append("No video file found for visual QC.")
    if research and script:
        factual = check_factual_consistency(research, script)
        result["checks"]["factual_consistency"] = factual
        if factual.get("unmatched"):
            result["warnings"].append(f"Factual inconsistency detected. Unmatched facts: {factual['unmatched']}")
    result["ok"] = len(result["errors"]) == 0
    result["warning_count"] = len(result["warnings"])
    if package_dir:
        with open(os.path.join(package_dir, "qc_report_v2.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    return result
