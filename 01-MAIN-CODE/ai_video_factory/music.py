"""Music selection and basic beat detection utilities.

Uses librosa (optional) for beat onset detection. If librosa is not
available, falls back to a simple heuristic selection.
"""
import os
import random
from typing import List


def choose_music(music_folder: str) -> str:
    """Pick a music file from `music_folder`. Returns path or empty string."""
    if not os.path.isdir(music_folder):
        return ""
    files = [os.path.join(music_folder, f) for f in os.listdir(music_folder) if f.lower().endswith((".mp3", ".wav", ".ogg", ".m4a"))]
    if not files:
        return ""
    # prefer longer files -> assume higher chance to fit
    files.sort(key=lambda p: os.path.getsize(p), reverse=True)
    # pick top 3 random
    candidates = files[:3]
    return random.choice(candidates)


def detect_beats(audio_path: str) -> List[float]:
    """Return beat times in seconds using librosa if available.

    If librosa not installed, returns an empty list.
    """
    try:
        import librosa

        y, sr = librosa.load(audio_path, sr=None)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        times = librosa.frames_to_time(beats, sr=sr)
        return times.tolist()
    except Exception:
        return []
