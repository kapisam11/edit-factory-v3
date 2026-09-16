"""Simple automated editing helpers using the shared media runner."""
import re
from typing import List, Tuple

from .render_engine import run_ffmpeg, run_ffprobe, validate_media_output


def detect_non_silent_segments(
    video_path: str, silence_thresh: float = -35.0, min_silence_len: float = 0.4
) -> List[Tuple[float, float]]:
    """Use ffmpeg's silencedetect to find non-silent segments."""
    cmd = [
        "ffmpeg", "-i", video_path,
        "-af", f"silencedetect=noise={silence_thresh}dB:d={min_silence_len}",
        "-f", "null", "-",
    ]
    try:
        proc = run_ffmpeg(cmd, timeout=300, capture_output=True)
    except Exception as exc:
        raise RuntimeError(f"Silence detection failed: {exc}") from exc
    stderr = proc.stderr or ""
    silence_starts = [float(m.group(1)) for m in re.finditer(r"silence_start: ([0-9.]+)", stderr)]
    silence_ends = [float(m.group(1)) for m in re.finditer(r"silence_end: ([0-9.]+)", stderr)]

    segments: List[Tuple[float, float]] = []
    if not silence_starts and not silence_ends:
        dur = _get_duration(video_path)
        return [(0.0, dur)] if dur else []

    dur = _get_duration(video_path) or (silence_ends[-1] if silence_ends else 0.0)
    if dur <= 0:
        return []

    last = 0.0
    for end in silence_ends:
        if last < end - 0.05:
            segments.append((last, end))
        next_starts = [s for s in silence_starts if s > end]
        last = next_starts[0] if next_starts else dur

    return [
        (max(0.0, s), min(e, dur))
        for s, e in segments
        if e - s > 0.35
    ] + ([(last, dur)] if last < dur - 0.05 else [])


def _get_duration(path: str) -> float:
    """Return duration of media using shared ffprobe execution."""
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        result = run_ffprobe([
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ])
        return float((result.stdout or "").strip())
    except Exception:
        return 0.0


def trim_segment(video_path: str, start: float, end: float, out_path: str) -> str:
    """Safely trim a single segment using shared FFmpeg execution."""
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ss", str(start), "-to", str(end),
        "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    run_ffmpeg(cmd)
    validate_media_output(out_path)
    return out_path


def generate_trim_commands(
    video_path: str, segments: List[Tuple[float, float]], out_dir: str
) -> List[str]:
    cmds = []
    for i, (s, e) in enumerate(segments):
        out = f"{out_dir}/clip_{i:03d}.mp4"
        cmds.append(
            f'ffmpeg -y -i "{video_path}" -ss {s:.3f} -to {e:.3f} '
            f'-c:v libx264 -c:a aac -b:a 128k "{out}"'
        )
    return cmds
