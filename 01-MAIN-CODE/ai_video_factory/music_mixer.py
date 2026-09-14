"""Music analysis, beat-sync editing, and audio mixing.

Detects BPM and beat positions from music tracks.
Aligns video cuts to beats.
Mixes music + voiceover with ducking (sidechain compression).

Requires: librosa (optional but recommended), numpy
"""
import logging
import os
import subprocess
from typing import List, Optional, Tuple

import numpy as np

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
        # Also detect onset strength for sub-beat precision
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        onset_frames = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)
        onset_times = librosa.frames_to_time(onset_frames, sr=sr).tolist()
        # Merge beats + strong onsets
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
        # Snap start to previous beat
        new_s = s
        prev_beats = [b for b in beats if b <= s]
        if prev_beats:
            candidate = max(prev_beats)
            if abs(candidate - s) < 0.25:
                new_s = candidate

        # Snap end to nearest beat
        new_e = e
        local_beats = [b for b in beats if new_s < b <= e + 0.5]
        if local_beats:
            nearest = min(local_beats, key=lambda b: abs(b - e))
            if abs(nearest - e) < 0.35:
                new_e = nearest

        # Ensure minimum duration (1 beat at this BPM)
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
    """Mix video + background music + optional voiceover.

    Uses ffmpeg for sidechain ducking: music drops when voiceover speaks.

    Args:
        video_path: Source video (with or without audio)
        music_path: Background music track
        vo_path: Voiceover track (optional)
        output_path: Output path
        music_volume: Base music volume (0.0-1.0)
        duck_db: How much to duck music when VO speaks (negative dB)
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)
    if not os.path.exists(music_path):
        raise FileNotFoundError(music_path)

    # Build ffmpeg command
    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", music_path]
    inputs = 2
    filter_complex_parts = []

    # Video stream
    filter_complex_parts.append("[0:v]copy[vout]")

    # Music volume adjustment
    music_vol = int(music_volume * 100)
    filter_complex_parts.append(f"[1:a]volume={music_vol}[music]")

    if vo_path and os.path.exists(vo_path):
        cmd.extend(["-i", vo_path])
        inputs = 3
        # Sidechain ducking: music ducks when VO is present
        # [music][2:a] sidechaincompress
        filter_complex_parts.append(
            f"[music][2:a]sidechaincompress=threshold=0.02:ratio=4:attack=50:release=200"
            f":level_sc=1:mix={duck_db}[music_ducked]"
        )
        # Mix ducked music + VO
        filter_complex_parts.append(
            "[music_ducked][2:a]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
    else:
        # Just music, no VO
        filter_complex_parts.append("[music]acopy[aout]")

    filter_str = ";".join(filter_complex_parts)

    cmd.extend([
        "-filter_complex", filter_str,
        "-map", "[vout]",
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
    subprocess.run(cmd, check=True)
    return output_path


def add_music_to_video(
    video_path: str,
    music_path: str,
    output_path: str,
    music_volume: float = 0.2,
    loop: bool = True,
) -> str:
    """Simple mix: add background music to video, looping if needed."""
    # Get video duration
    try:
        import json
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", video_path],
            capture_output=True, text=True, check=True,
        )
        vid_dur = float(json.loads(probe.stdout)["format"]["duration"])
    except Exception:
        vid_dur = 60.0

    # Get music duration
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", music_path],
            capture_output=True, text=True, check=True,
        )
        music_dur = float(json.loads(probe.stdout)["format"]["duration"])
    except Exception:
        music_dur = 60.0

    # Build filter: loop music if shorter than video
    if loop and music_dur < vid_dur:
        loops = int(vid_dur / music_dur) + 1
        music_filter = f"aloop=loop={loops}:size=2e+09"
    else:
        music_filter = "acopy"

    vol = int(music_volume * 100)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", music_path,
        "-filter_complex",
        f"[1:a]{music_filter},volume={vol},afade=t=out:st={vid_dur-2}:d=2[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]

    logger.info("[MIX] Adding music: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return output_path
