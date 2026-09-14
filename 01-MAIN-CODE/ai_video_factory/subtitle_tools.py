"""Subtitle generation and multi-aspect render helpers."""
import os
import textwrap
from typing import List

from .render_engine import run_ffmpeg, validate_media_output


def script_to_srt(script: str, out_path: str, avg_words_per_second: float = 2.5) -> str:
    if avg_words_per_second <= 0:
        raise ValueError("avg_words_per_second must be positive")
    lines = [line.strip() for line in script.splitlines() if line.strip()]
    subs = []
    time_cursor = 0.0
    idx = 1
    for line in lines:
        for wrapped in textwrap.wrap(line, width=24):
            wc = len(wrapped.split())
            duration = max(0.8, wc / avg_words_per_second)
            start = time_cursor
            end = time_cursor + duration
            subs.append((idx, start, end, wrapped))
            idx += 1
            time_cursor = end
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for idx, start, end, text in subs:
            f.write(f"{idx}\n{_fmt_time(start)} --> {_fmt_time(end)}\n{text}\n\n")
    return out_path


def _fmt_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _escape_subtitle_filename(path: str) -> str:
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render_variants(input_video: str, srt_path: str, out_dir: str) -> List[str]:
    if not os.path.isfile(input_video):
        raise FileNotFoundError(input_video)
    if not os.path.isfile(srt_path):
        raise FileNotFoundError(srt_path)
    os.makedirs(out_dir, exist_ok=True)
    variants = []
    specs = [
        (1080, 1920, "vertical_9_16.mp4"),
        (1080, 1080, "square_1_1.mp4"),
        (1920, 1080, "landscape_16_9.mp4"),
    ]
    escaped_srt = _escape_subtitle_filename(os.path.abspath(srt_path))
    for w, h, name in specs:
        out = os.path.join(out_dir, name)
        vf = f"scale=w=min({w}\\,iw):h=min({h}\\,ih),pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,subtitles={escaped_srt}"
        cmd = [
            "ffmpeg", "-y", "-i", input_video,
            "-vf", vf,
            "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k", out,
        ]
        try:
            run_ffmpeg(cmd)
            validate_media_output(out)
            variants.append(out)
        except (OSError, RuntimeError):
            continue
    return variants
