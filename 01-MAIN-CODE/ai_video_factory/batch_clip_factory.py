"""Optional batch clip factory built on the existing, validated V3 pipeline.

The factory ranks non-overlapping windows from one source video, extracts each
window to its own workspace, and then sends each one through the normal V3
production/QC stages. It never edits the source in place; one failed candidate
does not prevent the remaining candidates from being attempted.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .media_metadata import extract_media_metadata
from .production_guardrails import sha256_file
from .production_models import ProductionResult, Scene
from .render_engine import run_ffmpeg, validate_media_output
from .scene_intelligence import analyze_video
from .v3_exceptions import V3InputError
from .v3_pipeline import run_v3_pipeline


class BatchClipError(RuntimeError):
    """A batch-level configuration, analysis, or source-media failure."""


@dataclass(frozen=True)
class ClipCandidate:
    """One explainably scored source interval, before its render is attempted."""

    candidate_id: str
    start: float
    end: float
    score: float
    anchor_scene_id: str
    excerpt: str
    reasons: tuple[str, ...]
    scene_ids: tuple[str, ...]

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["duration"] = round(self.duration, 3)
        return item


def _finite_number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return result if math.isfinite(result) else default


def _value(scene: Any, key: str, default: Any = None) -> Any:
    if isinstance(scene, Mapping):
        return scene.get(key, default)
    return getattr(scene, key, default)


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, _finite_number(value)))


_TOPIC_STOP_WORDS = {
    "a", "about", "an", "and", "are", "as", "at", "be", "best", "by", "clip",
    "clips", "do", "for", "from", "how", "i", "in", "is", "it", "its", "make",
    "making", "me", "moment", "moments", "of", "on", "or", "short", "shorts",
    "some", "the", "this", "that", "to", "video", "what", "when", "where", "which",
    "who", "why", "with",
}


def _topic_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9']+", str(value).lower())
        if len(token) >= 2 and token not in _TOPIC_STOP_WORDS
    }


def _joined_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item) for item in value if item is not None)
    return str(value or "")


def _scene_signals(scene: Any, topic: str = "") -> dict[str, float]:
    transcript = _joined_text(_value(scene, "transcript", "")).strip()
    description = _joined_text(_value(scene, "description", "")).strip()
    # Scene-analysis fallback descriptions contain only timestamps and must not
    # be mistaken for source-content evidence or topic matches.
    if description.lower().startswith("source footage from "):
        description = ""
    objects = _joined_text(_value(scene, "objects", [])).strip()
    visible_text = _joined_text(_value(scene, "text", [])).strip()
    content_text = " ".join(part for part in (transcript, description, objects, visible_text) if part)
    content_tokens = _topic_tokens(content_text)
    topic_tokens = _topic_tokens(topic)
    topic_relevance = (
        len(topic_tokens & content_tokens) / len(topic_tokens)
        if topic_tokens and content_tokens
        else 0.0
    )
    face_count = max(0, int(_finite_number(_value(scene, "face_count", 0))))
    evidence_words = len(content_text.split())
    evidence = min(1.0, evidence_words / 15.0)
    if objects or visible_text:
        evidence = min(1.0, evidence + 0.25)
    signals = {
        "importance": _clamp01(_value(scene, "importance_score", 0.0)),
        "motion": _clamp01(_value(scene, "motion_score", 0.0)),
        "audio": _clamp01(_value(scene, "audio_energy", 0.0)),
        "faces": min(1.0, face_count / 2.0),
        "evidence": evidence,
        "topic": topic_relevance,
    }
    base_score = (
        0.42 * signals["importance"]
        + 0.22 * signals["motion"]
        + 0.18 * signals["audio"]
        + 0.10 * signals["faces"]
        + 0.08 * signals["evidence"]
    )
    # Topic relevance is used only when real scene text exists. Without a
    # transcript or OCR/description evidence, never invent a semantic penalty.
    signals["score"] = (
        0.82 * base_score + 0.18 * topic_relevance
        if topic_tokens and content_tokens
        else base_score
    )
    return signals


def _reasons(signals: Mapping[str, float]) -> tuple[str, ...]:
    labels = {
        "importance": (0.42, "High scene-importance signal"),
        "motion": (0.22, "Strong visual motion"),
        "audio": (0.18, "Elevated audio activity"),
        "faces": (0.10, "Faces are present"),
        "evidence": (0.08, "Transcript or visual evidence is available"),
        "topic": (0.15, "Matches words from the requested topic"),
    }
    contributions = sorted(
        (
            (weight * _clamp01(signals.get(key, 0.0)), label)
            for key, (weight, label) in labels.items()
        ),
        reverse=True,
    )
    selected = [label for value, label in contributions if value >= 0.045][:3]
    return tuple(selected or ["Highest available combined scene signal"])


def select_clip_candidates(
    scenes: Sequence[Any],
    source_duration: float,
    *,
    clip_seconds: float = 25.0,
    count: int = 5,
    min_gap_seconds: float = 1.0,
    topic: str = "",
) -> list[ClipCandidate]:
    """Rank windows and greedily select the best non-overlapping candidates.

    Scores are deterministic editorial heuristics, not predicted view counts.
    The requested count is a maximum: short sources can physically fit fewer
    non-overlapping windows.
    """
    duration = _finite_number(source_duration, -1.0)
    target = _finite_number(clip_seconds, -1.0)
    gap = _finite_number(min_gap_seconds, -1.0)
    if duration < 8.0:
        raise BatchClipError("Source duration must be at least 8 seconds.")
    if target < 8.0 or target > 180.0:
        raise BatchClipError("clip_seconds must be between 8 and 180 seconds.")
    try:
        requested_count = int(count)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BatchClipError("count must be an integer between 1 and 10.") from exc
    if requested_count != count or not 1 <= requested_count <= 10:
        raise BatchClipError("count must be an integer between 1 and 10.")
    if gap < 0.0 or gap > 600.0:
        raise BatchClipError("min_gap_seconds must be between 0 and 600 seconds.")

    clip_length = min(duration, target)
    usable: list[tuple[Any, float, float, dict[str, float]]] = []
    for index, scene in enumerate(scenes):
        start = _finite_number(_value(scene, "start", -1.0), -1.0)
        end = _finite_number(_value(scene, "end", -1.0), -1.0)
        if start < 0 or end <= start or start >= duration:
            continue
        start = max(0.0, min(duration, start))
        end = max(start, min(duration, end))
        if end <= start:
            continue
        usable.append((scene, start, end, _scene_signals(scene, topic)))
    if not usable:
        raise BatchClipError("Scene analysis returned no usable time intervals.")

    raw_candidates: list[tuple[float, float, float, Any, dict[str, float]]] = []
    seen_starts: list[float] = []
    for anchor, anchor_start, anchor_end, anchor_signals in usable:
        center = (anchor_start + anchor_end) / 2.0
        window_start = max(0.0, min(center - clip_length / 2.0, duration - clip_length))
        window_end = min(duration, window_start + clip_length)
        if any(abs(window_start - prior) < 0.75 for prior in seen_starts):
            continue
        seen_starts.append(window_start)

        weighted_total = 0.0
        weighted_score = 0.0
        overlapping_ids: list[str] = []
        for scene, scene_start, scene_end, signals in usable:
            overlap = max(0.0, min(window_end, scene_end) - max(window_start, scene_start))
            if overlap <= 0:
                continue
            weighted_total += overlap
            weighted_score += overlap * signals["score"]
            scene_id = str(_value(scene, "id", "") or "")
            if scene_id and scene_id not in overlapping_ids:
                overlapping_ids.append(scene_id)
        average = weighted_score / weighted_total if weighted_total else anchor_signals["score"]
        # The anchor keeps short high-value moments meaningful while the
        # surrounding window score still rewards clips with broader substance.
        combined = 0.70 * anchor_signals["score"] + 0.30 * average
        raw_candidates.append(
            (window_start, window_end, combined, anchor, anchor_signals | {"score": combined, "_average": average})
        )

    raw_candidates.sort(
        key=lambda item: (-item[2], item[0], str(_value(item[3], "id", "")))
    )
    selected: list[ClipCandidate] = []
    for start, end, score, anchor, signals in raw_candidates:
        if any(
            start < chosen.end + gap and end > chosen.start - gap
            for chosen in selected
        ):
            continue
        excerpt = str(
            _value(anchor, "transcript", "")
            or _value(anchor, "description", "")
            or ""
        ).strip()
        excerpt = re.sub(r"\s+", " ", excerpt)[:400]
        scene_ids = tuple(
            str(_value(scene, "id", "") or "")
            for scene, scene_start, scene_end, _ in usable
            if max(0.0, min(end, scene_end) - max(start, scene_start)) > 0
            and str(_value(scene, "id", "") or "")
        )
        selected.append(
            ClipCandidate(
                candidate_id=f"clip_{len(selected) + 1:02d}",
                start=round(start, 3),
                end=round(end, 3),
                score=round(max(0.0, min(100.0, score * 100.0)), 2),
                anchor_scene_id=str(_value(anchor, "id", "") or ""),
                excerpt=excerpt,
                reasons=_reasons(signals),
                scene_ids=scene_ids,
            )
        )
        if len(selected) >= requested_count:
            break
    return selected


def _parse_subtitle_timestamp(value: str) -> float:
    """Convert an SRT/VTT timestamp into seconds."""
    raw = str(value).strip().replace(",", ".")
    parts = raw.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60.0 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600.0 + int(minutes) * 60.0 + float(seconds)
    raise ValueError("Unsupported subtitle timestamp")


def _parse_timed_subtitles(path: Path) -> list[tuple[float, float, str]]:
    """Read timestamped SRT/VTT captions without requiring a new dependency."""
    try:
        raw_text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise BatchClipError(f"Could not read transcript file: {exc}") from exc
    timestamp_re = re.compile(
        r"(?m)^\s*(?P<start>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})"
        r"\s*-->\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3})"
        r"(?:[ \t][^\r\n]*)?\s*$"
    )
    matches = list(timestamp_re.finditer(raw_text))
    cues: list[tuple[float, float, str]] = []
    for index, match in enumerate(matches):
        try:
            start = _parse_subtitle_timestamp(match.group("start"))
            end = _parse_subtitle_timestamp(match.group("end"))
        except (TypeError, ValueError, OverflowError):
            continue
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(raw_text)
        body = raw_text[match.end():next_start]
        body = re.sub(r"<[^>]*>", " ", body)
        body = re.sub(r"\{\\an\d+\}", " ", body)
        lines = [
            html.unescape(line).strip()
            for line in body.splitlines()
            if line.strip() and not re.fullmatch(r"\s*\d+\s*", line)
        ]
        text_value = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if not text_value or start < 0 or end <= start:
            continue
        cues.append((start, end, text_value[:1000]))
    return cues


def _attach_subtitle_transcripts(
    scenes: Sequence[Any],
    cues: Sequence[tuple[float, float, str]],
) -> list[Any]:
    """Associate caption cues with source scenes by temporal overlap."""
    enriched: list[Any] = []
    for scene in scenes:
        try:
            scene_start = _finite_number(_value(scene, "start", 0.0))
            scene_end = _finite_number(_value(scene, "end", 0.0))
            existing = _joined_text(_value(scene, "transcript", "")).strip()
            matching = [
                text_value
                for cue_start, cue_end, text_value in cues
                if min(scene_end, cue_end) > max(scene_start, cue_start)
            ]
            combined = re.sub(
                r"\s+", " ", " ".join(part for part in [existing, *matching] if part)
            ).strip()
            if isinstance(scene, Mapping):
                clone = dict(scene)
                clone["transcript"] = combined
            else:
                try:
                    clone = replace(scene, transcript=combined)
                except TypeError:
                    clone = scene
                    setattr(clone, "transcript", combined)
            enriched.append(clone)
        except (AttributeError, TypeError, ValueError) as exc:
            raise BatchClipError(
                f"Could not attach transcript evidence to source scenes: {exc}"
            ) from exc
    return enriched


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _extract_segment(
    source: Path,
    destination: Path,
    *,
    start: float,
    duration: float,
) -> dict[str, Any]:
    """Create and validate a separate normalized MP4 segment atomically."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.partial.mp4")
    if destination.exists() or temporary.exists():
        raise BatchClipError(f"Refusing to overwrite an existing segment: {destination.name}")
    try:
        run_ffmpeg(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-y",
                "-ss", f"{start:.3f}", "-i", str(source),
                "-t", f"{duration:.3f}",
                "-map", "0:v:0", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart",
                str(temporary),
            ],
            timeout=max(300, min(3600, int(duration * 30))),
        )
        report = validate_media_output(str(temporary), require_video=True, require_audio=False)
        measured = _finite_number((report.get("format") or {}).get("duration"), 0.0)
        if measured <= 0 or abs(measured - duration) > max(1.5, duration * 0.08):
            raise BatchClipError("Extracted clip duration did not meet the segment contract.")
        os.replace(temporary, destination)
        return report
    finally:
        temporary.unlink(missing_ok=True)


def _short_error(exc: BaseException) -> str:
    value = re.sub(r"\s+", " ", str(exc)).strip()
    return f"{type(exc).__name__}: {value[:500]}" if value else type(exc).__name__


def run_batch_clip_factory(
    input_video: str | Path,
    topic: str,
    output_dir: str | Path,
    *,
    count: int = 5,
    clip_seconds: float = 25.0,
    min_gap_seconds: float = 1.0,
    platform: str = "youtube_shorts",
    audience: str = "general short-form viewers",
    bpm: int = 120,
    edit_type: str | None = None,
    model_key: str | None = None,
    music_path: str | None = None,
    enable_ocr: bool = False,
    enable_object_detection: bool = True,
    enable_diarization: bool = False,
    diarization_token: str | None = None,
    transcript_path: str | Path | None = None,
) -> dict[str, Any]:
    """Produce a bounded batch of independently validated Short packages."""
    source = Path(input_video).expanduser().resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise BatchClipError("input_video must point to a non-empty local file.")
    clean_topic = str(topic or "").strip()
    if not clean_topic or len(clean_topic) > 500:
        raise BatchClipError("topic must be non-empty and no longer than 500 characters.")
    try:
        requested_count = int(count)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BatchClipError("count must be an integer between 1 and 10.") from exc
    if requested_count != count or not 1 <= requested_count <= 10:
        raise BatchClipError("count must be an integer between 1 and 10.")
    requested_seconds = _finite_number(clip_seconds, -1.0)
    requested_gap = _finite_number(min_gap_seconds, -1.0)
    if not 8.0 <= requested_seconds <= 180.0:
        raise BatchClipError("clip_seconds must be between 8 and 180 seconds.")
    if not 0.0 <= requested_gap <= 600.0:
        raise BatchClipError("min_gap_seconds must be between 0 and 600 seconds.")
    if str(platform) not in {"youtube_shorts", "tiktok", "instagram_reels", "square", "youtube"}:
        raise BatchClipError("platform is not supported by V3.")
    if not str(audience or "").strip() or len(str(audience)) > 500:
        raise BatchClipError("audience must be non-empty and no longer than 500 characters.")
    transcript_file: Path | None = None
    transcript_cues: list[tuple[float, float, str]] = []
    if transcript_path is not None:
        transcript_file = Path(transcript_path).expanduser().resolve()
        if not transcript_file.is_file() or transcript_file.stat().st_size <= 0:
            raise BatchClipError("transcript_path must point to a non-empty local .srt or .vtt file.")
        if transcript_file.stat().st_size > 10_000_000:
            raise BatchClipError("transcript file must be no larger than 10 MB.")
        if transcript_file.suffix.lower() not in {".srt", ".vtt"}:
            raise BatchClipError("transcript_path supports timestamped .srt and .vtt subtitle files.")
        transcript_cues = _parse_timed_subtitles(transcript_file)
        if not transcript_cues:
            raise BatchClipError("The transcript file contains no valid timestamped subtitle cues.")
    target = Path(output_dir).expanduser().resolve()
    if target == source or target.is_relative_to(source):
        raise BatchClipError("output_dir must be a directory separate from the input video.")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BatchClipError("output_dir must be a new or empty directory; existing outputs are never overwritten.")
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "batch_manifest.json"
    try:
        with manifest_path.open("x", encoding="utf-8") as reservation:
            reservation.write("{}\n")
    except FileExistsError as exc:
        raise BatchClipError("A batch_manifest.json already exists in output_dir; choose a new directory.") from exc

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "analyzing",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "source": {"name": source.name, "sha256": None, "duration_seconds": None},
        "options": {
            "requested_count": requested_count,
            "clip_seconds": requested_seconds,
            "min_gap_seconds": requested_gap,
            "platform": str(platform),
            "topic": clean_topic,
            "transcript_sidecar": transcript_file.name if transcript_file else None,
        },
        "candidates": [],
        "summary": {"successful": 0, "failed": 0, "skipped": 0},
        "warnings": [],
    }
    try:
        source_hash = sha256_file(source)
        manifest["source"]["sha256"] = source_hash
        media_info = extract_media_metadata(source)
        streams = media_info.get("streams") or []
        if not any(
            isinstance(stream, Mapping) and stream.get("codec_type") == "video"
            for stream in streams
        ):
            raise BatchClipError("Source media must contain a video stream.")
        source_duration = _finite_number((media_info.get("format") or {}).get("duration"), 0.0)
        if source_duration < 8.0:
            raise BatchClipError("Source duration must be at least 8 seconds.")
        manifest["source"]["duration_seconds"] = round(source_duration, 3)
        _atomic_write_json(manifest_path, manifest)

        scenes: Sequence[Scene] = analyze_video(
            str(source),
            sample_seconds=2.5,
            max_scenes=240,
            min_scenes=0,
            enable_ocr=enable_ocr,
        )
        if transcript_cues:
            scenes = _attach_subtitle_transcripts(scenes, transcript_cues)
        candidates = select_clip_candidates(
            scenes,
            source_duration,
            clip_seconds=clip_seconds,
            count=requested_count,
            min_gap_seconds=requested_gap,
            topic=clean_topic,
        )
        manifest["candidates"] = [
            {
                **candidate.to_dict(),
                "status": "queued",
                "package_dir": candidate.candidate_id,
                "source_segment": f"{candidate.candidate_id}/source_segment.mp4",
                "final_video": None,
                "error": None,
            }
            for candidate in candidates
        ]
        if len(candidates) < int(count):
            manifest["warnings"].append(
                f"Selected {len(candidates)} non-overlapping clips; the source did not contain enough distinct windows for the requested {count}."
            )
        _atomic_write_json(manifest_path, manifest)

        for item in manifest["candidates"]:
            candidate_dir = target / item["package_dir"]
            item["status"] = "processing"
            _atomic_write_json(manifest_path, manifest)
            try:
                segment = candidate_dir / "source_segment.mp4"
                _extract_segment(
                    source,
                    segment,
                    start=float(item["start"]),
                    duration=float(item["duration"]),
                )
                clip_topic = f"{clean_topic[:470].rstrip()} — moment {item['candidate_id'][-2:]}"
                context_parts = [
                    f"Batch clip extracted from {source.name}.",
                    f"Original source time: {item['start']:.3f}s to {item['end']:.3f}s.",
                    f"Editorial ranking: {item['score']:.2f}/100.",
                    "Selection signals: " + "; ".join(item["reasons"]) + ".",
                ]
                if item.get("excerpt"):
                    context_parts.append("Moment evidence: " + str(item["excerpt"])[:400])
                result: ProductionResult = run_v3_pipeline(
                    str(segment),
                    clip_topic,
                    str(candidate_dir),
                    context="\n".join(context_parts)[:2000],
                    target_seconds=float(item["duration"]),
                    platform=platform,
                    audience=audience,
                    bpm=bpm,
                    edit_type=edit_type,
                    model_key=model_key,
                    music_path=music_path,
                    enable_ocr=enable_ocr,
                    enable_object_detection=enable_object_detection,
                    enable_diarization=enable_diarization,
                    diarization_token=diarization_token,
                )
                final_video = Path(result.final_video).resolve() if result.final_video else None
                if final_video is not None:
                    try:
                        final_video.relative_to(candidate_dir.resolve())
                    except ValueError:
                        final_video = None
                if not result.ok or final_video is None or not final_video.is_file():
                    item["status"] = "failed"
                    item["error"] = "; ".join(result.errors[:5])[:700] or "V3 pipeline did not return a valid final video inside the candidate package."
                else:
                    report = validate_media_output(str(final_video), require_video=True, require_audio=False)
                    item["status"] = "succeeded"
                    item["final_video"] = str(final_video.relative_to(target))
                    item["final_duration_seconds"] = round(
                        _finite_number((report.get("format") or {}).get("duration"), 0.0), 3
                    )
                    item["warnings"] = list(result.warnings[:10])
            except Exception as exc:  # independent candidate boundary: record and continue
                item["status"] = "failed"
                item["error"] = _short_error(exc)
            finally:
                manifest["summary"]["successful"] = sum(
                    entry["status"] == "succeeded" for entry in manifest["candidates"]
                )
                manifest["summary"]["failed"] = sum(
                    entry["status"] == "failed" for entry in manifest["candidates"]
                )
                manifest["summary"]["skipped"] = sum(
                    entry["status"] == "skipped" for entry in manifest["candidates"]
                )
                _atomic_write_json(manifest_path, manifest)

        current_hash = sha256_file(source)
        if current_hash != source_hash:
            manifest["status"] = "failed"
            manifest["warnings"].append(
                "Source SHA-256 changed during the batch. Outputs are retained for inspection but this run is not marked successful."
            )
        else:
            successful = manifest["summary"]["successful"]
            failed = manifest["summary"]["failed"]
            manifest["status"] = (
                "complete" if successful == len(candidates) and successful > 0
                else "partial" if successful > 0
                else "failed"
            )
            if failed and successful:
                manifest["warnings"].append(
                    "Some candidates failed independently; successful candidate packages have been retained."
                )
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(manifest_path, manifest)
        return manifest
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = _short_error(exc)
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(manifest_path, manifest)
        raise BatchClipError(str(exc)) from exc


__all__ = [
    "BatchClipError",
    "ClipCandidate",
    "run_batch_clip_factory",
    "select_clip_candidates",
]
