"""Optional, production-oriented intelligence layers for Edit Factory v2.

Heavy integrations are lazy-loaded. When available, this module provides
model-backed object detection, word-level Edge TTS timing, speaker diarization,
face-aware subtitle placement, ASS caption choreography, and music-aware cut
planning.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


@dataclass
class Detection:
    label: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass
class WordTimestamp:
    word: str
    start: float
    end: float
    speaker: Optional[str] = None


@dataclass
class CaptionCue:
    text: str
    start: float
    end: float
    x: float
    y: float
    anchor: str = "center"
    speaker: Optional[str] = None
    emphasis: bool = False


MOBILENET_LABELS = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse", "motorbike",
    "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor",
]


def detect_objects_mobilenet(frame: Any, *, model_path: Optional[str] = None, config_path: Optional[str] = None, confidence_threshold: float = 0.35) -> List[Detection]:
    import cv2  # type: ignore

    model_path = model_path or os.environ.get("EDIT_FACTORY_MOBILENET_MODEL", ".models/mobilenet_ssd/mobilenet.caffemodel")
    config_path = config_path or os.environ.get("EDIT_FACTORY_MOBILENET_CONFIG", ".models/mobilenet_ssd/deploy.prototxt")
    if not (os.path.exists(model_path) and os.path.exists(config_path)):
        raise FileNotFoundError("MobileNet-SSD model/config not found")
    net = cv2.dnn.readNetFromCaffe(config_path, model_path)
    h, w = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
    net.setInput(blob)
    detections = net.forward()
    out: List[Detection] = []
    for i in range(detections.shape[2]):
        confidence = float(detections[0, 0, i, 2])
        if confidence < confidence_threshold:
            continue
        idx = int(detections[0, 0, i, 1])
        label = MOBILENET_LABELS[idx] if 0 <= idx < len(MOBILENET_LABELS) else f"class_{idx}"
        x1, y1, x2, y2 = detections[0, 0, i, 3:7] * [w, h, w, h]
        out.append(Detection(label, confidence, max(0.0, min(float(x1), w)), max(0.0, min(float(y1), h)), max(0.0, min(float(x2), w)), max(0.0, min(float(y2), h))))
    return out


def extract_faces(frame: Any) -> List[Tuple[float, float, float, float]]:
    import cv2  # type: ignore
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cascade = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
    h, w = frame.shape[:2]
    return [(x / w, y / h, (x + fw) / w, (y + fh) / h) for x, y, fw, fh in faces]


def choose_caption_position(*, face_boxes: Sequence[Tuple[float, float, float, float]] = (), object_boxes: Sequence[Tuple[float, float, float, float]] = (), preferred: str = "bottom") -> Tuple[float, float]:
    candidates = {"top": (0.5, 0.16), "bottom": (0.5, 0.84), "left": (0.18, 0.52), "right": (0.82, 0.52)}
    ordered = [preferred] + [k for k in candidates if k != preferred]

    def penalty(px: float, py: float) -> float:
        score = 0.0
        for x1, y1, x2, y2 in list(face_boxes) + list(object_boxes):
            dx = max(0.0, max(x1 - px, px - x2))
            dy = max(0.0, max(y1 - py, py - y2))
            score += 1.0 / (0.04 + dx * dx + dy * dy)
        return score

    best = min(ordered, key=lambda key: penalty(*candidates[key]))
    return candidates[best]


def generate_word_timestamps(text: str, output_audio: str, *, voice: str = "en-US-GuyNeural", timestamps_path: Optional[str] = None) -> List[WordTimestamp]:
    """Generate Edge TTS audio and collect real WordBoundary timings."""
    import edge_tts  # type: ignore
    timestamps: List[WordTimestamp] = []

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice)
        with open(output_audio, "wb") as audio:
            async for message in communicate.stream():
                if message["type"] == "audio":
                    audio.write(message["data"])
                elif message["type"] == "WordBoundary":
                    start = float(message.get("offset", 0)) / 10_000_000.0
                    duration = float(message.get("duration", 0)) / 10_000_000.0
                    word = str(message.get("text", "")).strip()
                    if word:
                        timestamps.append(WordTimestamp(word, start, start + max(0.01, duration)))

    os.makedirs(os.path.dirname(output_audio) or ".", exist_ok=True)
    asyncio.run(_run())
    if timestamps_path:
        save_json(timestamps_path, [asdict(item) for item in timestamps])
    return timestamps


def diarize_audio(audio_path: str, *, hf_token: Optional[str] = None) -> List[WordTimestamp]:
    """Run pyannote speaker diarization; requires a Hugging Face token."""
    from pyannote.audio import Pipeline  # type: ignore
    token = hf_token or os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("PYANNOTE_AUTH_TOKEN")
    if not token:
        raise RuntimeError("Set HUGGINGFACE_TOKEN or PYANNOTE_AUTH_TOKEN for speaker diarization")
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization", use_auth_token=token)
    diarization = pipeline(audio_path)
    return [WordTimestamp("", float(turn.start), float(turn.end), str(speaker)) for turn, _, speaker in diarization.itertracks(yield_label=True)]


def assign_speakers(words: Sequence[WordTimestamp], diarization: Sequence[WordTimestamp]) -> List[WordTimestamp]:
    out: List[WordTimestamp] = []
    for word in words:
        best, best_overlap = None, 0.0
        for segment in diarization:
            overlap = max(0.0, min(word.end, segment.end) - max(word.start, segment.start))
            if overlap > best_overlap:
                best_overlap, best = overlap, segment.speaker
        out.append(WordTimestamp(word.word, word.start, word.end, best))
    return out


def build_choreographed_captions(words: Sequence[WordTimestamp], *, face_boxes_by_time: Optional[Sequence[Tuple[float, Sequence[Tuple[float, float, float, float]]]]] = None, object_boxes_by_time: Optional[Sequence[Tuple[float, Sequence[Tuple[float, float, float, float]]]]] = None, max_words: int = 5) -> List[CaptionCue]:
    """Group word timings into readable, speaker-aware, spatially-safe cues."""
    cues: List[CaptionCue] = []
    if not words:
        return cues
    faces = face_boxes_by_time or []
    objects = object_boxes_by_time or []

    def at_time(t: float, source: Sequence[Tuple[float, Sequence[Tuple[float, float, float, float]]]]) -> Sequence[Tuple[float, float, float, float]]:
        return min(source, key=lambda pair: abs(pair[0] - t))[1] if source else []

    bucket: List[WordTimestamp] = []
    for word in words:
        split = bool(bucket) and (len(bucket) >= max_words or word.speaker != bucket[0].speaker)
        if split:
            t0, t1 = bucket[0].start, bucket[-1].end
            x, y = choose_caption_position(face_boxes=at_time((t0 + t1) / 2, faces), object_boxes=at_time((t0 + t1) / 2, objects))
            cues.append(CaptionCue(" ".join(w.word for w in bucket), t0, t1, x, y, speaker=bucket[0].speaker, emphasis=True))
            bucket = []
        bucket.append(word)
    if bucket:
        t0, t1 = bucket[0].start, bucket[-1].end
        x, y = choose_caption_position(face_boxes=at_time((t0 + t1) / 2, faces), object_boxes=at_time((t0 + t1) / 2, objects))
        cues.append(CaptionCue(" ".join(w.word for w in bucket), t0, t1, x, y, speaker=bucket[0].speaker, emphasis=True))
    return cues


def _ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, centis = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{centis:02d}"


def write_ass_captions(cues: Sequence[CaptionCue], output_path: str, *, width: int = 1080, height: int = 1920) -> str:
    """Write ASS subtitles using absolute positions from the choreography engine."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "",
        "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,5,2,5,40,40,40,1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        x, y = int(cue.x * width), int(cue.y * height)
        text = cue.text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        if cue.speaker:
            text = f"[{cue.speaker}] {text}"
        lines.append(f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Default,,0,0,0,,{{\\an5\\pos({x},{y})}}{text}")
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return output_path


def analyze_music_profile(audio_path: str) -> Dict[str, Any]:
    import librosa  # type: ignore
    import numpy as np  # type: ignore
    y, sr = librosa.load(audio_path, sr=None, mono=True, duration=180)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
    rms = librosa.feature.rms(y=y)[0]
    times = librosa.frames_to_time(range(len(rms)), sr=sr).tolist()
    peak = float(np.max(rms)) if len(rms) else 1.0
    energy = [round(float(v) / max(peak, 1e-9), 4) for v in rms]
    return {"bpm": float(tempo), "beats": beat_times, "times": times, "energy": energy}


def music_aware_cut_plan(durations: Sequence[float], profile: Dict[str, Any], *, energy_bias: float = 0.7) -> List[Tuple[float, float]]:
    """Build sequential cuts snapped to the musical grid and energy profile."""
    beats = [float(v) for v in profile.get("beats", [])]
    times = [float(v) for v in profile.get("times", [])]
    energy = [float(v) for v in profile.get("energy", [])]
    if not beats:
        cursor = 0.0
        plan: List[Tuple[float, float]] = []
        for duration in durations:
            end = cursor + max(0.25, float(duration))
            plan.append((round(cursor, 3), round(end, 3)))
            cursor = end
        return plan

    plan = []
    cursor = beats[0]
    for index, duration in enumerate(durations):
        target = cursor + float(duration)
        candidates = [b for b in beats if b > cursor + 0.25 and b <= target + 1.0]
        if candidates:
            if energy and times:
                def score(b: float) -> float:
                    j = min(range(len(times)), key=lambda k: abs(times[k] - b))
                    boost = energy_bias * energy[j] if index >= len(durations) * 0.55 else 0.0
                    return abs(b - target) - boost
                end = min(candidates, key=score)
            else:
                end = min(candidates, key=lambda b: abs(b - target))
        else:
            end = target
        end = max(end, cursor + 0.25)
        plan.append((round(cursor, 3), round(end, 3)))
        cursor = end
    return plan


def save_json(path: str, payload: Any) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    return path


def detect_video_objects(video_path: str, *, sample_seconds: float = 2.5) -> List[Dict[str, Any]]:
    import cv2  # type: ignore
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    duration = frame_count / fps if fps > 0 else 0.0
    model_path = os.environ.get("EDIT_FACTORY_MOBILENET_MODEL", ".models/mobilenet_ssd/mobilenet.caffemodel")
    config_path = os.environ.get("EDIT_FACTORY_MOBILENET_CONFIG", ".models/mobilenet_ssd/deploy.prototxt")
    output: List[Dict[str, Any]] = []
    timestamp = 0.0
    try:
        while timestamp <= duration:
            cap.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, frame = cap.read()
            if ok:
                detections = detect_objects_mobilenet(frame, model_path=model_path, config_path=config_path)
                output.extend({"time": round(timestamp, 3), **asdict(d)} for d in detections)
            timestamp += max(0.5, sample_seconds)
    finally:
        cap.release()
    return output
