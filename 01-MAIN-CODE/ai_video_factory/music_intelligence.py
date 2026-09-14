"""Music analysis helpers used by the production planner.

Librosa is optional. Without it the module still returns a valid duration-only
map so the rest of the pipeline can run.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Dict, List, Optional


def _duration(path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=20)
        return max(0.0, float(result.stdout.strip()))
    except Exception:
        return 0.0


def analyze_music(path: str, *, hop_length: int = 512) -> Dict[str, Any]:
    if not path or not os.path.exists(path):
        raise FileNotFoundError(path)
    duration = _duration(path)
    payload: Dict[str, Any] = {
        "version": 1,
        "path": os.path.abspath(path),
        "duration": duration,
        "beats": [],
        "energy": [],
        "backend": "duration_only",
    }

    try:
        import librosa  # type: ignore
        y, sr = librosa.load(path, sr=None, mono=True)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, hop_length=hop_length)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
        rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
        if len(rms):
            lo, hi = float(rms.min()), float(rms.max())
            scale = max(1e-9, hi - lo)
            energy = [round(max(0.0, min(1.0, (float(v) - lo) / scale)), 4) for v in rms]
        else:
            energy = []
        payload.update({
            "backend": "librosa",
            "tempo_bpm": round(float(tempo), 3),
            "beats": [round(float(v), 3) for v in beat_times.tolist()],
            "energy": energy,
            "hop_length": hop_length,
            "energy_duration": round(len(energy) * hop_length / max(1, sr), 3),
        })
    except Exception:
        pass
    return payload


def energy_at(music_map: Dict[str, Any], timestamp: float) -> float:
    energy: List[float] = list(music_map.get("energy") or [])
    if not energy:
        return 0.5
    duration = max(0.001, float(music_map.get("energy_duration") or music_map.get("duration") or 1.0))
    index = int(max(0.0, min(duration - 1e-6, timestamp)) / duration * len(energy))
    return float(energy[max(0, min(len(energy) - 1, index))])


def beat_strength(music_map: Dict[str, Any], timestamp: float, tolerance: float = 0.08) -> float:
    beats = [float(v) for v in music_map.get("beats") or []]
    if not beats:
        return 0.0
    nearest = min(abs(timestamp - beat) for beat in beats)
    return max(0.0, 1.0 - nearest / max(tolerance, 1e-6))


def transition_score(music_map: Dict[str, Any], timestamp: float) -> float:
    """Score a candidate cut using both beat proximity and local energy."""
    return round(0.60 * beat_strength(music_map, timestamp) + 0.40 * energy_at(music_map, timestamp), 4)


def save_music_map(music_map: Dict[str, Any], path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(music_map, handle, indent=2)
    return path
