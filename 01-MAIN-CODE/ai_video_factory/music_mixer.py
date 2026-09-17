"""Music analysis, beat-sync editing, and audio mixing.

Detects BPM and beat positions from music tracks.
Aligns video cuts to beats.
Mixes music + voiceover with ducking (sidechain compression).

Requires: librosa (optional but recommended), numpy
"""
import json
import logging
import os
from typing import List, Optional, Tuple

import numpy as np

from .render_engine import run_ffmpeg, run_ffprobe, validate_media_output

logger = logging.getLogger(__name__)

try:
    import librosa  # type: ignore
except Exception:
    librosa = None


def detect_music_beats(audio_path: str) -> Tuple[Optional[float], Optional[List[float]]]:
    """Detect BPM and beat timestamps from a music track.

    Returns (bpm, [beat_times]) or (None, None) if analysis fails.
    """
    if not librosa:
        logger.warning("librosa not installed; music beat detection unavailable")
        return None, None
    try:
        y, sr = librosa.load(audio_path, sr=None, mono=True, duration=120)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)
        onset_times = librosa.frames_to_time(onset_frames, sr=sr).tolist()
        all_impacts = sorted(set(beat_times + onset_times))
        return float(tempo), all_impacts
    except Exception as e:
        logger.warning("Music beat detection failed: %s", e)
    return None, None


def find_nearest_beat(time_sec: float, beats: List[float], tolerance: float = 0.3) -> Optional[float]:
    """Find the nearest beat to a given time, if within tolerance."""
    if not beats:
        return None
    nearest = min(beats, key=lambda b: abs(b - time_sec))
    if abs(nearest - time_sec) <= tolerance:
        return nearest
    return None


def align_segments_to_music(
    segments: List[Tuple[float, float]],
    beats: List[float],
    bpm: float,
) -> List[Tuple[float, float]]:
    """Snap segment boundaries to nearest music beats.

    Returns adjusted (start, end) segments where cuts land on beats.
    """
    if not beats or not bpm:
        return segments

    aligned = []
    for s, e in segments:
        new_s = s
        prev_beats = [b for b in beats if b <= s]
        if prev_beats:
            candidate = max(prev_beats)
            if abs(candidate - s) < 0.25:
                new_s = candidate

        new_e = e
        local_beats = [b for b in beats if new_s < b <= e + 0.5]
        if local_beats:
            nearest = min(local_beats, key=lambda b: abs(b - e))
            if abs(nearest - e) < 0.35:
                new_e = nearest

        beat_dur = 60.0 / bpm
        if new_e - new_s < beat_dur * 0.5:
            new_e = new_s + beat_dur

        aligned.append((round(new_s, 3), round(new_e, 3)))
    return aligned


def mix_audio(
    video_path: str,
    music_path: str,
    vo_path: Optional[str],
    output_path: str,
    music_volume: float = 0.25,
    duck_db: float = -12.0,
) -> str:
    """Mix video + background music + optional voiceover."""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(video_path)
    if not os.path.isfile(music_path):
        raise FileNotFoundError(music_path)
    if vo_path and not os.path.isfile(vo_path):
        raise FileNotFoundError(vo_path)

    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", music_path]
    if vo_path:
        cmd.extend(["-i", vo_path])

    music_vol = int(max(0.0, min(1.0, music_volume)) * 100)
    filter_complex_parts = [f"[1:a]volume={music_vol}[music]"]

    if vo_path:
        filter_complex_parts.append(
            "[music][2:a]sidechaincompress=threshold=0.02:ratio=4:attack=50:release=200:"
            f"level_sc=1:mix={duck_db}[music_ducked]"
        )
        filter_complex_parts.append(
            "[music_ducked][2:a]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
    else:
        filter_complex_parts.append("[music]acopy[aout]")

    cmd.extend([
        "-filter_complex", ";".join(filter_complex_parts),
        "-map", "0:v:0",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ])

    logger.info("[MIX] Running: %s", " ".join(cmd))
    run_ffmpeg(cmd)
    validate_media_output(output_path, require_video=True, require_audio=True)
    return output_path


def _probe_duration(path: str, label: str) -> float:
    try:
        probe = run_ffprobe(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            timeout=20,
        )
    except Exception as exc:
        raise RuntimeError(f"Could not determine {label} duration for {path}: {exc}") from exc
    if probe.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {label} {path}: {(probe.stderr or 'unknown error').strip()[-1000:]}")
    try:
        duration = float(json.loads(probe.stdout or "{}")["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"ffprobe returned invalid {label} duration for {path}") from exc
    if duration <= 0:
        raise RuntimeError(f"ffprobe returned a non-positive {label} duration for {path}")
    return duration


def add_music_to_video(
    video_path: str,
    music_path: str,
    output_path: str,
    music_volume: float = 0.2,
    loop: bool = True,
) -> str:
    """Simple mix: add background music to video, looping if needed."""
    if not os.path.isfile(video_path):
        raise FileNotFoundError(video_path)
    if not os.path.isfile(music_path):
        raise FileNotFoundError(music_path)

    vid_dur = _probe_duration(video_path, "video")
    music_dur = _probe_duration(music_path, "music")

    if loop and music_dur < vid_dur:
        loops = int(vid_dur / music_dur) + 1
        music_filter = f"aloop=loop={loops}:size=2e+09"
    else:
        music_filter = "acopy"

    vol = int(max(0.0, min(1.0, music_volume)) * 100)
    fade_start = max(0.0, vid_dur - 2.0)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", music_path,
        "-filter_complex",
        f"[1:a]{music_filter},volume={vol},afade=t=out:st={fade_start:.3f}:d=2[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v:0",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]

    logger.info("[MIX] Adding music: %s", " ".join(cmd))
    run_ffmpeg(cmd)
    validate_media_output(output_path, require_video=True, require_audio=True)
    return output_path
