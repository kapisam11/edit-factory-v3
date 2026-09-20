"""AI Video Factory — Styled Subtitle Burn-In."""
import json
import math
import re
import subprocess

from .ffmpeg_budget import run_ffmpeg_subprocess
import shutil
from typing import Dict, List, Optional


SUBTITLE_STYLES = {
    "bold_white": {"fontcolor": "white", "fontsize": 64, "borderw": 4, "bordercolor": "black", "y": "(h*0.85)", "font": "Arial", "shadowx": 2, "shadowy": 2, "shadowcolor": "black@0.5"},
    "bold_yellow": {"fontcolor": "yellow", "fontsize": 68, "borderw": 5, "bordercolor": "black", "y": "(h*0.82)", "font": "Arial", "shadowx": 2, "shadowy": 2, "shadowcolor": "black@0.6"},
    "elegant_white": {"fontcolor": "white", "fontsize": 56, "borderw": 2, "bordercolor": "black@0.7", "y": "(h*0.88)", "font": "Times New Roman", "shadowx": 1, "shadowy": 1, "shadowcolor": "black@0.3"},
    "clear_white": {"fontcolor": "white", "fontsize": 60, "borderw": 3, "bordercolor": "black", "y": "(h*0.85)", "font": "Arial"},
}


def _run_ffmpeg(command: List[str], timeout: int = 600) -> subprocess.CompletedProcess:
    try:
        return run_ffmpeg_subprocess(command, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg timed out after {timeout}s") from exc


def _escape_drawtext(text: str) -> str:
    # FFmpeg filter syntax treats backslash, single quote, colon, and percent specially.
    return (str(text).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%"))


def estimate_word_timing(script: str, total_duration: float) -> List[Dict]:
    if not math.isfinite(float(total_duration)) or total_duration <= 0:
        raise ValueError("total_duration must be positive and finite")
    words = re.findall(r"\w+", str(script))
    if not words:
        return []
    avg_duration = total_duration / len(words)
    return [{"word": w, "start": i * avg_duration, "end": (i + 1) * avg_duration} for i, w in enumerate(words)]


def build_drawtext_filter(word_timings: List[Dict], style: str = "bold_white") -> str:
    style_cfg = SUBTITLE_STYLES.get(style, SUBTITLE_STYLES["bold_white"])
    lines = []
    cur = []
    for wt in word_timings:
        if not isinstance(wt, dict) or "word" not in wt:
            raise ValueError("word timing entries must be objects containing word")
        cur.append(wt)
        if len(cur) >= 5:
            lines.append(cur); cur = []
    if cur: lines.append(cur)
    parts = []
    for line in lines:
        text = _escape_drawtext(" ".join(str(w["word"]) for w in line))
        start = float(line[0]["start"]); end = float(line[-1]["end"])
        if not all(math.isfinite(v) for v in (start, end)) or end <= start:
            raise ValueError("invalid subtitle timing")
        y = style_cfg.get("y", "(h*0.85)")
        ft = (f"drawtext=text='{text}':fontcolor={style_cfg['fontcolor']}:fontsize={style_cfg['fontsize']}:"
              f"borderw={style_cfg.get('borderw',0)}:bordercolor={style_cfg.get('bordercolor','black')}:x=(w-text_w)/2:y={y}:"
              f"enable='between(t\\,{start:.6f}\\,{end:.6f})'")
        if "shadowx" in style_cfg:
            ft += f":shadowx={style_cfg['shadowx']}:shadowy={style_cfg['shadowy']}:shadowcolor={style_cfg['shadowcolor']}"
        parts.append(ft)
    return ",".join(parts)


def burn_subtitles(input_video: str, output_video: str, script: str, style: str = "bold_white", total_duration: Optional[float] = None) -> str:
    if total_duration is None:
        try:
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", input_video], capture_output=True, text=True, check=True, timeout=30)
            info = json.loads(probe.stdout or "{}")
            total_duration = float(info.get("format", {}).get("duration", 0) or 0)
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Could not probe subtitle input duration: {exc}") from exc
        if not math.isfinite(total_duration) or total_duration <= 0:
            raise RuntimeError("Input video has no valid positive duration")
    word_timings = estimate_word_timing(script, total_duration)
    drawtext = build_drawtext_filter(word_timings, style)
    if not drawtext:
        shutil.copy2(input_video, output_video)
        return output_video
    _run_ffmpeg(["ffmpeg", "-y", "-i", input_video, "-vf", drawtext, "-c:a", "copy", "-c:v", "libx264", "-preset", "fast", "-crf", "23", output_video])
    return output_video


def generate_ass_subtitle(word_timings: List[Dict], output_path: str, style: str = "bold_white", video_width: int = 1080, video_height: int = 1920) -> str:
    style_cfg = SUBTITLE_STYLES.get(style, SUBTITLE_STYLES["bold_white"])
    if video_width <= 0 or video_height <= 0:
        raise ValueError("video dimensions must be positive")
    header = """[Script Info]\nTitle: AI Video Factory Subtitles\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,{font},{fontsize},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{outline},{shadow},2,10,10,30,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n""".format(w=video_width, h=video_height, font=style_cfg.get("font", "Arial"), fontsize=style_cfg["fontsize"], outline=style_cfg.get("borderw", 2), shadow=1 if "shadowx" in style_cfg else 0)
    def fmt(t: float) -> str:
        h = int(t // 3600); m = int((t % 3600) // 60); s = t % 60
        return f"{h}:{m:02d}:{s:05.2f}"
    lines = []; cur = []
    for wt in word_timings:
        cur.append(wt)
        if len(cur) >= 5:
            lines.append(cur); cur = []
    if cur: lines.append(cur)
    body = ""
    for line in lines:
        text = " ".join(str(w["word"]).replace("{", "\\{").replace("}", "\\}") for w in line)
        start = fmt(float(line[0]["start"])); end = fmt(float(line[-1]["end"]))
        body += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n"
    with open(output_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + body)
    return output_path
