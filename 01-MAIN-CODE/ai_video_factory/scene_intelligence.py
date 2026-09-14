"""Scene indexing for footage-aware editing.

Core analysis works without ML dependencies. When optional OpenCV and Tesseract
are installed, the analyzer can add shot-change, motion, face, and OCR signals.
Heavy object-detection models are deliberately opt-in through model paths.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
from dataclasses import asdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .production_models import Scene


class SceneAnalysisError(RuntimeError):
    pass


def _ffprobe_duration(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        value = float(result.stdout.strip())
        return max(0.0, value)
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _tokenize(text: str) -> List[str]:
    return [token.strip(".,!?;:()[]{}\"'").lower() for token in text.split() if token.strip()]


def _keyword_score(query: str, candidate: str) -> float:
    q = set(_tokenize(query))
    c = set(_tokenize(candidate))
    if not q or not c:
        return 0.0
    return len(q & c) / max(1, len(q))


def _motion_score(prev_gray, gray) -> float:
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        delta = cv2.absdiff(prev_gray, gray)
        return _clamp(float(np.mean(delta)) / 64.0)
    except Exception:
        return 0.0


def _frame_stats(frame) -> Tuple[float, int, Optional[object]]:
    """Return brightness, face count and grayscale frame."""
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = _clamp(float(np.mean(gray)) / 255.0)
        face_count = 0
        try:
            cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
            detector = cv2.CascadeClassifier(cascade_path)
            faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
            face_count = len(faces)
        except Exception:
            pass
        return brightness, face_count, gray
    except Exception:
        return 0.0, 0, None


def _ocr_frame(frame) -> List[str]:
    try:
        import pytesseract  # type: ignore
        text = pytesseract.image_to_string(frame).strip()
        return [line.strip() for line in text.splitlines() if line.strip()][:8]
    except Exception:
        return []


def _sample_frames(video_path: str, start: float, end: float, samples: int) -> Iterable[Tuple[float, object]]:
    try:
        import cv2  # type: ignore
    except Exception:
        return []

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    try:
        count = max(1, int(samples))
        duration = max(0.01, end - start)
        for i in range(count):
            timestamp = start + (duration * (i / max(1, count - 1)))
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, frame = cap.read()
            if ok:
                yield timestamp, frame
    finally:
        cap.release()


def analyze_video(
    video_path: str,
    *,
    sample_seconds: float = 2.5,
    max_scenes: int = 240,
    enable_ocr: bool = False,
) -> List[Scene]:
    """Analyze a source video into coarse scenes.

    The fallback path still creates a useful deterministic scene index from
    duration alone. OpenCV augments scenes with motion/brightness/faces/OCR.
    """
    if not video_path or not os.path.exists(video_path):
        raise SceneAnalysisError(f"Video does not exist: {video_path}")

    duration = _ffprobe_duration(video_path)
    if duration <= 0:
        try:
            import cv2  # type: ignore
            cap = cv2.VideoCapture(video_path)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
            if fps > 0 and frames > 0:
                duration = frames / fps
        except Exception:
            duration = 0.0

    if duration <= 0:
        raise SceneAnalysisError("Unable to determine source video duration")

    step = max(0.5, float(sample_seconds))
    count = min(max_scenes, max(1, int(math.ceil(duration / step))))
    edges = [min(duration, i * duration / count) for i in range(count + 1)]

    scenes: List[Scene] = []
    global_prev_gray = None
    for index in range(count):
        start = float(edges[index])
        end = float(edges[index + 1])
        frames = list(_sample_frames(video_path, start, end, 2))
        motions: List[float] = []
        brightness_values: List[float] = []
        face_counts: List[int] = []
        ocr_text: List[str] = []
        local_prev = global_prev_gray

        for _, frame in frames:
            brightness, faces, gray = _frame_stats(frame)
            brightness_values.append(brightness)
            face_counts.append(faces)
            if enable_ocr:
                ocr_text.extend(_ocr_frame(frame))
            if local_prev is not None and gray is not None:
                motions.append(_motion_score(local_prev, gray))
            if gray is not None:
                local_prev = gray

        global_prev_gray = local_prev
        motion = sum(motions) / len(motions) if motions else 0.0
        brightness = sum(brightness_values) / len(brightness_values) if brightness_values else 0.0
        faces = max(face_counts) if face_counts else 0
        importance = _clamp(0.45 * motion + 0.25 * min(1.0, faces / 2.0) + 0.30 * (0.5 + abs(brightness - 0.5)))

        scene = Scene(
            id=f"scene_{index:04d}",
            start=round(start, 3),
            end=round(end, 3),
            description=f"Source footage from {start:.1f}s to {end:.1f}s",
            objects=[],
            text=sorted(set(ocr_text))[:12],
            motion_score=round(_clamp(motion), 4),
            brightness=round(_clamp(brightness), 4),
            face_count=int(faces),
            importance_score=round(importance, 4),
            source=video_path,
        )
        scenes.append(scene)

    return scenes


def enrich_scene_descriptions(
    scenes: Sequence[Scene],
    *,
    transcript_chunks: Optional[Sequence[str]] = None,
) -> List[Scene]:
    """Attach coarse transcript chunks without requiring a speech model."""
    if not transcript_chunks:
        return list(scenes)
    chunks = list(transcript_chunks)
    if not chunks:
        return list(scenes)
    out: List[Scene] = []
    for index, scene in enumerate(scenes):
        text = chunks[min(index, len(chunks) - 1)]
        scene.transcript = str(text)
        scene.description = f"{scene.description}; {text}" if text else scene.description
        out.append(scene)
    return out


def score_scene(scene: Scene, query: str = "") -> float:
    """Score a scene for selection by combining relevance and visual salience."""
    relevance = _keyword_score(query, scene.searchable_text)
    return round(_clamp(0.55 * relevance + 0.45 * scene.importance_score), 4)


def save_scene_index(scenes: Sequence[Scene], output_path: str, source_video: str = "") -> str:
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    payload = {
        "version": 1,
        "source_video": source_video,
        "scene_count": len(scenes),
        "scenes": [asdict(scene) for scene in scenes],
    }
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return output_path


def load_scene_index(path: str) -> List[Scene]:
    with open(path, "r", encoding="utf-8") as handle:
        payload: Dict = json.load(handle)
    return [Scene(**item) for item in payload.get("scenes", [])]
