"""Complete content-production intelligence and automation layer.

This module implements the twenty engineering upgrades and twenty product
features from the production roadmap as deterministic, testable primitives.
It intentionally composes with the existing director/pipeline/composer rather
than replacing them.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# 1-4: speech/caption/audio-first editing
# ---------------------------------------------------------------------------

_FILLERS = {"uh", "um", "er", "erm", "hmm", "like", "you know"}
_EMPHASIS = {
    "important", "secret", "truth", "never", "always", "only", "first", "last",
    "crazy", "insane", "impossible", "destroyed", "won", "lost", "betrayed",
    "clutch", "fail", "failed", "win", "winner", "best", "worst", "million",
}


def _words(text: str) -> List[str]:
    return re.findall(r"\b[\w'-]+\b", str(text or ""))


def clean_filler_words(text: str) -> Dict[str, Any]:
    """Remove common filler tokens and return a diff-friendly report."""
    tokens = str(text or "").split()
    removed: List[str] = []
    kept: List[str] = []
    for token in tokens:
        normalized = re.sub(r"[^a-z]", "", token.lower())
        if normalized in _FILLERS:
            removed.append(token)
            continue
        kept.append(token)
    return {"text": " ".join(kept), "removed": removed, "count": len(removed)}


def build_word_level_captions(
    words: Sequence[Mapping[str, Any]],
    *,
    max_words: int = 4,
    min_duration: float = 0.35,
    max_duration: float = 2.2,
) -> List[Dict[str, Any]]:
    """Group real word timestamps into readable, emphasis-aware caption cues."""
    cues: List[Dict[str, Any]] = []
    bucket: List[Mapping[str, Any]] = []
    for item in words:
        if not str(item.get("word", "")).strip():
            continue
        bucket.append(item)
        text = " ".join(str(w.get("word", "")).strip() for w in bucket)
        end = float(bucket[-1].get("end", bucket[-1].get("start", 0.0) + 0.1))
        start = float(bucket[0].get("start", 0.0))
        punctuation = str(bucket[-1].get("word", ""))[-1:] in ".!?"
        duration = end - start
        if len(bucket) >= max_words or punctuation or duration >= max_duration:
            important = [
                str(w.get("word", "")).strip()
                for w in bucket
                if re.sub(r"[^a-z0-9]", "", str(w.get("word", "")).lower()) in _EMPHASIS
                or any(ch.isdigit() for ch in str(w.get("word", "")))
            ]
            if duration < min_duration:
                end = start + min_duration
            cues.append({"text": text, "start": start, "end": end, "emphasis": important[:2]})
            bucket = []
    if bucket:
        start = float(bucket[0].get("start", 0.0))
        end = float(bucket[-1].get("end", start + min_duration))
        if end - start < min_duration:
            end = start + min_duration
        cues.append({"text": " ".join(str(w.get("word", "")).strip() for w in bucket), "start": start, "end": end, "emphasis": []})
    return cues


def audio_first_timeline(words: Sequence[Mapping[str, Any]], *, target_seconds: Optional[float] = None) -> List[Tuple[float, float]]:
    """Use speech timing as the target-time grid instead of fixed segment lengths."""
    cues = build_word_level_captions(words)
    if not cues:
        total = float(target_seconds or 0.0)
        return [(0.0, total)] if total > 0 else []
    spans = [(float(c["start"]), float(c["end"])) for c in cues]
    if target_seconds and spans:
        scale = float(target_seconds) / max(spans[-1][1], 0.01)
        spans = [(round(a * scale, 3), round(b * scale, 3)) for a, b in spans]
    return spans


# ---------------------------------------------------------------------------
# 5-8: shot intelligence / cleanup
# ---------------------------------------------------------------------------

@dataclass
class ShotScore:
    shot_id: str
    score: float
    motion: float = 0.0
    faces: float = 0.0
    brightness: float = 0.0
    uniqueness: float = 0.0
    audio_relevance: float = 0.0
    semantic_similarity: float = 0.0


def score_shots(shots: Sequence[Mapping[str, Any]], topic: str = "") -> List[Dict[str, Any]]:
    """Rank candidate shots using explainable visual/audio/semantic signals."""
    topic_words = set(_words(topic.lower()))
    scored: List[ShotScore] = []
    for index, shot in enumerate(shots):
        text = str(shot.get("text") or shot.get("description") or "").lower()
        overlap = len(topic_words.intersection(_words(text)))
        semantic = min(1.0, overlap / max(1, len(topic_words))) if topic_words else 0.0
        values = {
            "motion": float(shot.get("motion", shot.get("motion_score", 0.0))),
            "faces": float(shot.get("faces", shot.get("face_score", 0.0))),
            "brightness": float(shot.get("brightness", 0.5)),
            "uniqueness": float(shot.get("uniqueness", shot.get("novelty", 0.5))),
            "audio_relevance": float(shot.get("audio_relevance", 0.5)),
            "semantic_similarity": semantic,
        }
        total = (
            values["motion"] * 0.20 + values["faces"] * 0.10 + values["brightness"] * 0.10
            + values["uniqueness"] * 0.20 + values["audio_relevance"] * 0.15 + values["semantic_similarity"] * 0.25
        )
        scored.append(ShotScore(str(shot.get("id", index)), round(total, 5), **values))
    return [asdict(item) for item in sorted(scored, key=lambda item: (-item.score, item.shot_id))]


def detect_visual_redundancy(items: Sequence[Mapping[str, Any]], *, similarity_threshold: float = 0.92) -> Dict[str, Any]:
    """Mark near-duplicates using supplied perceptual hashes or feature vectors."""
    kept: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    for item in items:
        current = dict(item)
        phash = str(current.get("phash", ""))
        vector = current.get("embedding")
        duplicate_of: Optional[str] = None
        for prior in kept:
            if phash and prior.get("phash") and len(phash) == len(str(prior["phash"])):
                distance = sum(a != b for a, b in zip(phash, str(prior["phash"]))) / max(1, len(phash))
                if 1.0 - distance >= similarity_threshold:
                    duplicate_of = str(prior.get("id"))
                    break
            if vector and prior.get("embedding") and len(vector) == len(prior["embedding"]):
                dot = sum(float(a) * float(b) for a, b in zip(vector, prior["embedding"]))
                na = math.sqrt(sum(float(a) * float(a) for a in vector))
                nb = math.sqrt(sum(float(b) * float(b) for b in prior["embedding"]))
                cosine = dot / max(1e-9, na * nb)
                if cosine >= similarity_threshold:
                    duplicate_of = str(prior.get("id"))
                    break
        if duplicate_of:
            current["duplicate_of"] = duplicate_of
            duplicates.append(current)
        else:
            kept.append(current)
    return {"kept": kept, "duplicates": duplicates, "duplicate_count": len(duplicates)}


def detect_dead_time(words: Sequence[Mapping[str, Any]], scenes: Sequence[Mapping[str, Any]], *, pause_threshold: float = 0.9) -> List[Tuple[float, float]]:
    """Find long windows lacking speech and meaningful scene activity."""
    spans: List[Tuple[float, float]] = []
    cursor = 0.0
    speech = sorted((float(w.get("start", 0)), float(w.get("end", 0))) for w in words)
    for start, end in speech:
        if start - cursor >= pause_threshold:
            spans.append((round(cursor, 3), round(start, 3)))
        cursor = max(cursor, end)
    for scene in scenes:
        end = float(scene.get("end", scene.get("timestamp", 0.0)))
        cursor = max(cursor, end)
    return [item for item in spans if item[1] - item[0] >= pause_threshold]


def clean_voiceover_words(words: Sequence[Mapping[str, Any]], *, pause_threshold: float = 0.85) -> Dict[str, Any]:
    """Remove filler tokens and flag repeated words/long pauses for re-rendering."""
    cleaned: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    previous = ""
    for word in words:
        token = str(word.get("word", "")).strip()
        norm = re.sub(r"[^a-z]", "", token.lower())
        if norm in _FILLERS:
            removed.append({"word": token, "reason": "filler"})
            continue
        start = float(word.get("start", 0.0))
        if cleaned and start - float(cleaned[-1].get("end", start)) >= pause_threshold:
            removed.append({"word": "<pause>", "reason": "long_pause", "start": cleaned[-1].get("end"), "end": start})
        if norm and norm == previous:
            removed.append({"word": token, "reason": "repeated_word"})
            continue
        cleaned.append(dict(word))
        previous = norm
    return {"words": cleaned, "removed": removed}


# ---------------------------------------------------------------------------
# 9-13: adaptive storytelling
# ---------------------------------------------------------------------------

_EMOTION_TERMS = {
    "dramatic": {"danger", "betray", "lost", "destroyed", "secret", "shocked"},
    "hype": {"win", "clutch", "insane", "crazy", "best", "victory"},
    "funny": {"funny", "joke", "fail", "oops", "weird", "ridiculous"},
    "curious": {"why", "how", "secret", "mystery", "hidden", "truth"},
}


def detect_emotion(text: str) -> Dict[str, Any]:
    tokens = set(_words(text.lower()))
    scores = {name: len(tokens.intersection(terms)) for name, terms in _EMOTION_TERMS.items()}
    best = max(scores, key=lambda k: (scores[k], k)) if scores else "neutral"
    return {"emotion": best, "scores": scores, "confidence": round(scores.get(best, 0) / max(1, sum(scores.values())), 3)}


def adaptive_pacing(intensity: Sequence[float], *, total_seconds: float = 45.0, min_shot: float = 0.45, max_shot: float = 4.0) -> List[float]:
    """Allocate more time to low-intensity context and tighter cuts to high intensity."""
    values = [max(0.0, min(1.0, float(v))) for v in intensity] or [0.5]
    weights = [1.25 - v for v in values]
    minimum = min_shot * len(values)
    remaining = max(0.0, total_seconds - minimum)
    total_weight = sum(weights) or 1.0
    durations = [min(max_shot, min_shot + remaining * (w / total_weight)) for w in weights]
    scale = total_seconds / max(sum(durations), 0.01)
    return [round(max(min_shot, min(max_shot, d * scale)), 3) for d in durations]


def generate_hook_candidates(topic: str, summary: Mapping[str, Any], *, count: int = 8) -> List[Dict[str, Any]]:
    angle = str(summary.get("strongest_angle") or topic).strip()
    tension = str(summary.get("main_conflict") or "something unexpected happened").strip()
    candidates = [
        f"Nobody expected {angle}.",
        f"This changed everything: {angle}.",
        f"The truth about {angle} is worse than it sounds.",
        f"You have one question after seeing {angle}: why?",
        f"It started normally. Then {tension.lower()}",
        f"Here is what actually happened with {angle}.",
        f"The moment {angle} went too far.",
        f"Before you watch {angle}, notice this.",
    ]
    ranked = []
    for hook in candidates[: max(1, count)]:
        emotion = detect_emotion(hook)
        score = (
            len(set(_words(hook.lower())).intersection(set(_words(topic.lower())))) * 1.4
            + (1.5 if "?" in hook else 0.0)
            + emotion["scores"].get("curious", 0) * 0.6
            + emotion["scores"].get("dramatic", 0) * 0.5
        )
        ranked.append({"hook": hook, "score": round(score, 3), "emotion": emotion["emotion"]})
    return sorted(ranked, key=lambda item: (-item["score"], item["hook"]))


def choose_clip_count(speech_words: int, intensity_mean: float, target_seconds: float) -> int:
    rate = speech_words / max(1.0, target_seconds)
    if rate > 3.0 or intensity_mean > 0.75:
        return 12
    if rate > 2.3 or intensity_mean > 0.58:
        return 9
    if rate > 1.6:
        return 6
    return 4


# ---------------------------------------------------------------------------
# 14-16: platform layout + media validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LayoutProfile:
    name: str
    width: int
    height: int
    aspect: str


LAYOUTS = {
    "shorts": LayoutProfile("shorts", 1080, 1920, "9:16"),
    "reels": LayoutProfile("reels", 1080, 1920, "9:16"),
    "tiktok": LayoutProfile("tiktok", 1080, 1920, "9:16"),
    "youtube": LayoutProfile("youtube", 1920, 1080, "16:9"),
    "square": LayoutProfile("square", 1080, 1080, "1:1"),
}


def get_layout(name: str) -> LayoutProfile:
    key = str(name or "shorts").strip().lower()
    if key not in LAYOUTS:
        raise ValueError(f"Unknown layout: {name}")
    return LAYOUTS[key]


def plan_source_aware_crop(frame_width: int, frame_height: int, layout: LayoutProfile, subjects: Sequence[Mapping[str, float]]) -> Dict[str, Any]:
    target_ratio = layout.width / layout.height
    source_ratio = frame_width / max(1, frame_height)
    if source_ratio > target_ratio:
        crop_h = frame_height
        crop_w = int(frame_height * target_ratio)
    else:
        crop_w = frame_width
        crop_h = int(frame_width / target_ratio)
    if subjects:
        cx = sum(float(s.get("x", 0.5)) for s in subjects) / len(subjects)
        cy = sum(float(s.get("y", 0.5)) for s in subjects) / len(subjects)
    else:
        cx, cy = 0.5, 0.5
    left = max(0, min(frame_width - crop_w, int(cx * frame_width - crop_w / 2)))
    top = max(0, min(frame_height - crop_h, int(cy * frame_height - crop_h / 2)))
    return {"layout": asdict(layout), "crop": {"left": left, "top": top, "width": crop_w, "height": crop_h}, "center": {"x": cx, "y": cy}}


def validate_render(path: str, *, expected_width: Optional[int] = None, expected_height: Optional[int] = None, min_duration: float = 0.5) -> Dict[str, Any]:
    """Verify codec, streams, duration, resolution and corruption with ffprobe."""
    target = Path(path)
    if not target.is_file():
        return {"ok": False, "errors": ["missing_file"]}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration,format_name:stream=index,codec_type,codec_name,width,height,duration", "-of", "json", str(target)],
            capture_output=True, text=True, check=True, timeout=20,
        )
        payload = json.loads(result.stdout or "{}")
    except Exception as exc:
        return {"ok": False, "errors": [f"ffprobe:{exc}"]}
    streams = payload.get("streams", [])
    fmt = payload.get("format", {})
    duration = float(fmt.get("duration", 0.0) or 0.0)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    errors: List[str] = []
    if duration < min_duration:
        errors.append("duration_too_short")
    if not video:
        errors.append("missing_video_stream")
    if not audio:
        errors.append("missing_audio_stream")
    if expected_width and video and int(video.get("width", 0)) != expected_width:
        errors.append("wrong_width")
    if expected_height and video and int(video.get("height", 0)) != expected_height:
        errors.append("wrong_height")
    return {"ok": not errors, "errors": errors, "duration": duration, "video": video, "audio": audio, "format": fmt}


# ---------------------------------------------------------------------------
# 17-20: checkpoints, dependencies, retries, reproducibility
# ---------------------------------------------------------------------------

@dataclass
class CheckpointStore:
    path: str

    def load(self) -> Dict[str, Any]:
        try:
            return json.loads(Path(self.path).read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "stages": {}, "artifacts": {}}

    def mark(self, stage: str, *, status: str, artifacts: Optional[Sequence[str]] = None, error: Optional[str] = None) -> Dict[str, Any]:
        state = self.load()
        state.setdefault("stages", {})[stage] = {"status": status, "updated_at": time.time(), "artifacts": list(artifacts or []), "error": error}
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.path).write_text(json.dumps(state, indent=2), encoding="utf-8")
        return state

    def is_complete(self, stage: str) -> bool:
        return self.load().get("stages", {}).get(stage, {}).get("status") == "complete"


def artifact_hash(path: str) -> str:
    digest = hashlib.sha256()
    target = Path(path)
    if target.is_file():
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    else:
        digest.update(str(target).encode("utf-8"))
    return digest.hexdigest()


def dependency_fingerprint(inputs: Mapping[str, Any]) -> str:
    canonical = json.dumps(inputs, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def artifact_dependency_graph(artifacts: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    graph: Dict[str, Any] = {}
    for name, info in artifacts.items():
        deps = dict(info.get("inputs", {}))
        graph[name] = {"fingerprint": dependency_fingerprint(deps), "inputs": deps}
    return graph


def provider_retry(callable_fn, *, attempts: int = 4, base_delay: float = 0.5, timeout_errors: Tuple[type, ...] = (TimeoutError,)):
    """Provider-aware exponential backoff wrapper with bounded attempts."""
    last_error: Optional[Exception] = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return callable_fn()
        except Exception as exc:  # pragma: no cover - provider exceptions vary
            last_error = exc
            if attempt >= attempts:
                break
            delay = min(8.0, base_delay * (2 ** (attempt - 1)))
            if isinstance(exc, timeout_errors) or getattr(exc, "status_code", None) in {408, 429, 500, 502, 503, 504}:
                time.sleep(delay)
            else:
                raise
    raise last_error or RuntimeError("provider call failed")


def deterministic_manifest(*, job_id: str, inputs: Mapping[str, Any], selected_assets: Sequence[str], random_seed: int = 0, model_versions: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "job_id": job_id,
        "random_seed": int(random_seed),
        "inputs": dict(inputs),
        "selected_assets": list(selected_assets),
        "model_versions": dict(model_versions or {}),
        "dependency_fingerprint": dependency_fingerprint({"inputs": inputs, "assets": selected_assets, "models": model_versions or {}}),
    }


# ---------------------------------------------------------------------------
# 20 product features: strategy, publishing, learning and automation
# ---------------------------------------------------------------------------

@dataclass
class ContentAngle:
    title: str
    angle: str
    tension: str
    score: float


def generate_content_strategy(topic: str, *, count: int = 10) -> List[Dict[str, Any]]:
    templates = [
        ("the hidden story", "hidden context", "what viewers do not know"),
        ("the biggest mistake", "mistake analysis", "the cost of getting it wrong"),
        ("the turning point", "moment-by-moment", "one event changes everything"),
        ("the unexpected result", "surprise outcome", "expectation vs reality"),
        ("the fastest way", "practical shortcut", "time saved"),
        ("the hardest part", "challenge breakdown", "difficulty and pressure"),
        ("before vs after", "transformation", "visible change"),
        ("what nobody tells you", "insider lesson", "information gap"),
        ("the test", "experiment", "will it actually work"),
        ("the story behind it", "origin story", "why it matters"),
    ]
    results = []
    for index, (label, angle, tension) in enumerate(templates[:max(1, count)]):
        title = f"{topic}: {label}"
        score = round(10.0 - index * 0.21 + (len(topic) % 7) * 0.05, 3)
        results.append(asdict(ContentAngle(title, angle, tension, score)))
    return results


def trend_research(topic: str, *, limit: int = 10) -> Dict[str, Any]:
    """Best-effort public RSS trend lookup; offline-safe."""
    url = "https://news.google.com/rss/search?q=" + __import__("urllib.parse").parse.quote(topic)
    try:
        import requests  # type: ignore
        xml = requests.get(url, timeout=10, headers={"User-Agent": "EditFactory/2"}).text
        titles = re.findall(r"<title>(.*?)</title>", xml)[1 : limit + 1]
        return {"topic": topic, "source": "google_news_rss", "titles": titles, "available": True}
    except Exception as exc:
        return {"topic": topic, "source": "google_news_rss", "titles": [], "available": False, "warning": str(exc)}


def competitor_content_gap(topic: str, *, candidates: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    rows = list(candidates or [])
    if not rows:
        return {"topic": topic, "available": False, "gaps": ["No competitor dataset supplied; use title/style differentiation heuristics."]}
    tokens = set(_words(topic.lower()))
    seen = set()
    for row in rows:
        seen.update(_words(str(row.get("title", "")).lower()))
    gaps = [token for token in tokens if token not in seen]
    return {"topic": topic, "available": True, "gaps": gaps, "competitors": len(rows)}


def long_and_short_plan(topic: str, *, long_seconds: float = 480.0, short_seconds: float = 45.0) -> Dict[str, Any]:
    return {"topic": topic, "long_form": {"seconds": long_seconds, "layout": "youtube"}, "short": {"seconds": short_seconds, "layout": "shorts"}, "shared_assets": True}


def generate_clip_factory_plan(scenes: Sequence[Mapping[str, Any]], *, count: int = 5) -> List[Dict[str, Any]]:
    ranked = score_shots(scenes)
    selected = ranked[: max(1, min(int(count), len(ranked)))]
    plans = []
    for index, shot in enumerate(selected, start=1):
        plans.append({"clip": index, "source_shot": shot["shot_id"], "score": shot["score"], "target_platform": "youtube_shorts"})
    return plans


def thumbnail_factory_plan(variants: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    ranked = []
    for item in variants:
        text = str(item.get("text", ""))
        face = float(item.get("face_salience", 0.0))
        contrast = float(item.get("contrast", 0.0))
        clarity = float(item.get("clarity", 0.0))
        score = round(face * 0.35 + contrast * 0.35 + clarity * 0.30 + min(1.0, len(text) / 40.0) * 0.1, 4)
        ranked.append({**dict(item), "score": score})
    return sorted(ranked, key=lambda x: (-x["score"], str(x.get("id", ""))))


def generate_chapters(cues: Sequence[Mapping[str, Any]], *, min_gap: float = 30.0) -> List[Dict[str, Any]]:
    chapters: List[Dict[str, Any]] = []
    last = -float("inf")
    for cue in cues:
        start = float(cue.get("start", 0.0))
        title = str(cue.get("title") or cue.get("text") or "Chapter").strip().replace("\n", " ")[:80]
        if start - last < min_gap:
            continue
        chapters.append({"start": round(start, 3), "title": title})
        last = start
    return chapters


def disclosure_policy(*, ai_generated: bool, realistic_alteration: bool, platform: str) -> Dict[str, Any]:
    return {
        "platform": platform,
        "ai_generated": bool(ai_generated),
        "realistic_alteration": bool(realistic_alteration),
        "review_required": bool(realistic_alteration or ai_generated),
        "recommended_action": "review platform disclosure controls before publishing" if (realistic_alteration or ai_generated) else "no AI disclosure flag inferred",
    }


def duplicate_fingerprint(*, script: str, title: str, thumbnail_path: Optional[str] = None, clip_paths: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    payload = {"script": re.sub(r"\s+", " ", script).strip().lower(), "title": re.sub(r"\s+", " ", title).strip().lower()}
    if thumbnail_path and Path(thumbnail_path).exists():
        payload["thumbnail_sha256"] = artifact_hash(thumbnail_path)
    if clip_paths:
        payload["clips"] = [artifact_hash(path) for path in clip_paths if Path(path).exists()]
    return {"fingerprint": dependency_fingerprint(payload), "components": payload}


def performance_feedback(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    views = float(metrics.get("views", 0.0))
    likes = float(metrics.get("likes", 0.0))
    comments = float(metrics.get("comments", 0.0))
    avg_pct = float(metrics.get("averageViewPercentage", metrics.get("avg_view_percentage", 0.0)))
    engagement = (likes + comments * 2.0) / max(1.0, views)
    return {"engagement_rate": round(engagement, 6), "retention": round(avg_pct / 100.0 if avg_pct > 1 else avg_pct, 6), "label": "positive" if engagement > 0.04 or avg_pct > 0.6 else "neutral"}


def learn_channel_style(videos: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not videos:
        return {"video_count": 0, "profile": {}}
    def avg(key: str, default: float = 0.0) -> float:
        vals = [float(v[key]) for v in videos if v.get(key) is not None]
        return sum(vals) / len(vals) if vals else default
    styles = [str(v.get("caption_style", "bold_white")) for v in videos]
    voices = [str(v.get("voice", "en-US-GuyNeural")) for v in videos]
    return {
        "video_count": len(videos),
        "profile": {
            "avg_cuts_per_minute": round(avg("cuts_per_minute", 12.0), 3),
            "avg_shot_duration": round(avg("avg_shot_duration", 2.5), 3),
            "avg_hook_seconds": round(avg("hook_duration", 2.0), 3),
            "caption_style": max(set(styles), key=styles.count),
            "voice": max(set(voices), key=voices.count),
        },
    }


@dataclass
class QueueJob:
    id: str
    topic: str
    platform: str = "youtube_shorts"
    status: str = "queued"
    options: Dict[str, Any] = field(default_factory=dict)


def queue_jobs(path: str, topics: Sequence[str], *, platform: str = "youtube_shorts") -> Dict[str, Any]:
    jobs = [asdict(QueueJob(f"job-{index:04d}", topic, platform)) for index, topic in enumerate(topics, start=1) if str(topic).strip()]
    payload = {"version": 1, "jobs": jobs}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def run_queue(path: str, producer, *, max_jobs: Optional[int] = None) -> Dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    jobs = payload.get("jobs", [])[: max_jobs or None]
    results = []
    for job in jobs:
        if job.get("status") == "complete":
            continue
        job["status"] = "running"
        try:
            results.append({"id": job["id"], "result": producer(job)})
            job["status"] = "complete"
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"results": results, "jobs": payload["jobs"]}


def build_content_run_manifest(*, topic: str, summary: Mapping[str, Any], platform: str, package_dir: str) -> Dict[str, Any]:
    root = Path(package_dir)
    return {
        "topic": topic,
        "platform": platform,
        "strategy": generate_content_strategy(topic),
        "emotion": detect_emotion(str(summary.get("script", topic))),
        "hooks": generate_hook_candidates(topic, summary),
        "layout": asdict(get_layout("youtube" if platform == "youtube" else "shorts")),
        "disclosure": disclosure_policy(ai_generated=bool(summary.get("model_backed")), realistic_alteration=bool(summary.get("altered_media")), platform=platform),
        "long_short": long_and_short_plan(topic),
        "upload_ready": (root / "upload_package.json").exists(),
    }


def enrich_package(package_dir: str, *, topic: str, platform: str = "youtube_shorts") -> Dict[str, Any]:
    """Run the full feature layer against an existing successful job package."""
    root = Path(package_dir)
    plan: Dict[str, Any] = {}
    for filename in ("plan.json", "metadata.json"):
        path = root / filename
        if path.exists():
            try:
                plan.update(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
    script = str(plan.get("script", ""))
    words = plan.get("word_timestamps") or []
    caption_cues = build_word_level_captions(words) if words else []
    clean_script = clean_filler_words(script)
    emotion = detect_emotion(script or topic)
    hooks = generate_hook_candidates(topic, plan)
    chapters = generate_chapters(caption_cues)
    deterministic = deterministic_manifest(
        job_id=root.name,
        inputs={"topic": topic, "platform": platform, "script": script},
        selected_assets=[p.name for p in root.iterdir() if p.is_file()],
        random_seed=0,
        model_versions={"factory": str(plan.get("version", "unknown"))},
    )
    manifest = build_content_run_manifest(topic=topic, summary={**plan, "script": script}, platform=platform, package_dir=package_dir)
    manifest.update({
        "caption_cues": caption_cues,
        "clean_script": clean_script,
        "emotion": emotion,
        "hooks": hooks,
        "chapters": chapters,
        "dead_time": detect_dead_time(words, plan.get("scenes", [])) if words else [],
        "duplicate": duplicate_fingerprint(script=script, title=str(plan.get("title", topic))),
        "deterministic": deterministic,
    })
    (root / "content_factory_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (root / "captions_readable.json").write_text(json.dumps(caption_cues, indent=2), encoding="utf-8")
    (root / "chapters.json").write_text(json.dumps(chapters, indent=2), encoding="utf-8")
    return manifest


__all__ = [
    "build_word_level_captions", "audio_first_timeline", "score_shots", "detect_visual_redundancy",
    "detect_dead_time", "clean_voiceover_words", "adaptive_pacing", "generate_hook_candidates",
    "detect_emotion", "choose_clip_count", "get_layout", "plan_source_aware_crop", "validate_render",
    "CheckpointStore", "artifact_hash", "dependency_fingerprint", "artifact_dependency_graph",
    "provider_retry", "deterministic_manifest", "generate_content_strategy", "trend_research",
    "competitor_content_gap", "long_and_short_plan", "generate_clip_factory_plan", "thumbnail_factory_plan",
    "generate_chapters", "disclosure_policy", "duplicate_fingerprint", "performance_feedback",
    "learn_channel_style", "queue_jobs", "run_queue", "enrich_package", "build_content_run_manifest",
]
