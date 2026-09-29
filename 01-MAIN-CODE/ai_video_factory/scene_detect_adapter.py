"""Optional PySceneDetect adapter with a deterministic OpenCV fallback."""
from __future__ import annotations
import math
import os

def pyscenedetect_available() -> bool:
    try:
        import scenedetect  # type: ignore
    except ImportError:
        return False
    return True

def _fallback_boundaries(video_path: str, threshold: float = 0.42) -> list[float]:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return []
    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        return []
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    fps = fps if math.isfinite(fps) and fps > 0 else 25.0
    previous = None
    cuts: list[float] = []
    frame_index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % max(1, int(round(fps / 4.0))) != 0:
                frame_index += 1
                continue
            small = cv2.resize(frame, (96, 54))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            if previous is not None:
                distance = float(cv2.compareHist(previous, hist, cv2.HISTCMP_BHATTACHARYYA))
                if distance >= float(threshold):
                    cuts.append(frame_index / fps)
            previous = hist
            frame_index += 1
    finally:
        capture.release()
    return sorted(set(round(value, 3) for value in cuts if value > 0.0))

def detect_shot_boundaries(video_path: str, *, threshold: float = 27.0, min_scene_len: int = 10) -> list[float]:
    if not os.path.isfile(video_path):
        return []
    if pyscenedetect_available():
        try:
            from scenedetect import ContentDetector, SceneManager, open_video  # type: ignore
            video = open_video(video_path)
            manager = SceneManager()
            manager.add_detector(ContentDetector(threshold=float(threshold), min_scene_len=int(min_scene_len)))
            manager.detect_scenes(video=video)
            cuts = []
            for _, end in manager.get_scene_list():
                seconds = float(end.get_seconds())
                if math.isfinite(seconds) and seconds > 0:
                    cuts.append(round(seconds, 3))
            return cuts[:-1] if cuts else []
        except Exception:
            pass
    return _fallback_boundaries(video_path, threshold=max(0.1, min(0.95, float(threshold) / 100.0)))

def scene_windows(video_path: str, duration: float, *, max_scenes: int = 240) -> list[tuple[float, float]]:
    duration = float(duration)
    if not math.isfinite(duration) or duration <= 0:
        return []
    cuts = [0.0] + [value for value in detect_shot_boundaries(video_path) if 0.0 < value < duration] + [duration]
    cuts = sorted(set(cuts))
    if len(cuts) < 3 or len(cuts) - 1 > int(max_scenes):
        return []
    return [(round(cuts[index], 3), round(cuts[index + 1], 3)) for index in range(len(cuts) - 1)]

__all__ = ["detect_shot_boundaries", "pyscenedetect_available", "scene_windows"]
