"""Segment detection and beat-aligned trimming utilities."""
import logging
from typing import List, Tuple, Optional

from .edit_automation import detect_non_silent_segments, trim_segment, _get_duration

try:
    import librosa  # type: ignore
except Exception:
    librosa = None

logger = logging.getLogger(__name__)


def get_duration_safe(path: str) -> float:
    """Return a media duration or zero when probing fails."""
    try:
        return _get_duration(path)
    except Exception:
        return 0.0


# Backwards-compatible alias for older internal callers. New code should use
# the public get_duration_safe() name instead of depending on an underscore API.
def _get_duration_safe(path: str) -> float:
    return get_duration_safe(path)


def detect_beats(input_video: str, max_duration: float = 120.0) -> Optional[List[float]]:
    if not librosa:
        return None
    try:
        y, sr = librosa.load(input_video, sr=None, mono=True, duration=max_duration)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beats = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        return beats
    except Exception as e:
        logger.warning("Beat detection failed: %s", e)
        return None


def get_segments(input_video: str, edit_plan_length: int) -> List[Tuple[float, float]]:
    segments = detect_non_silent_segments(input_video)
    if not segments:
        dur = get_duration_safe(input_video)
        if dur <= 0:
            segments = [(0.0, 10.0)]
        else:
            step = max(2.0, min(6.0, dur / 8))
            segments = [(i, min(i + step, dur)) for i in [j * step for j in range(int(dur // step))]]
    return segments


def snap_to_beat(seg_start: float, seg_end: float, desired_duration: float, beats: Optional[List[float]]) -> float:
    desired_end = seg_start + desired_duration
    if not beats:
        return min(desired_end, seg_end)

    local_beats = [b for b in beats if seg_start <= b <= seg_end]
    if not local_beats:
        return min(desired_end, seg_end)

    nearest = min(local_beats, key=lambda b: abs(b - desired_end))
    if abs(nearest - desired_end) < 0.25:
        return min(nearest, seg_end)
    return min(desired_end, seg_end)


def generate_clip_paths(
    input_video: str,
    segments: List[Tuple[float, float]],
    edit_plan_length: int,
    temp_dir: str,
) -> List[str]:
    clip_paths = []
    for i, (s, e) in enumerate(segments[:edit_plan_length]):
        out = f"{temp_dir}/clip_{i:03d}.mp4"
        try:
            trim_segment(input_video, s, e, out)
            clip_paths.append(out)
        except Exception as exc:
            logger.error("Trim failed for segment %d: %s", i, exc)
    return clip_paths
