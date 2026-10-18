"""Opt-in, local-only speech transcription for source-footage understanding.

The transcriber accepts a local CTranslate2 faster-whisper model directory only.
It never resolves model names from a remote registry or uploads media.
"""
from __future__ import annotations

from bisect import bisect_right
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Sequence


class SourceTranscriptionError(RuntimeError):
    """A requested source transcription could not be produced safely."""


def _finite_number(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SourceTranscriptionError(f"Transcription returned an invalid {field}") from exc
    if not math.isfinite(parsed):
        raise SourceTranscriptionError(f"Transcription returned a non-finite {field}")
    return parsed


def _local_model_path(model_path: str | os.PathLike[str] | None = None) -> Path:
    configured = str(model_path or os.environ.get("AIVF_ASR_MODEL_PATH", "")).strip()
    if not configured:
        raise SourceTranscriptionError(
            "Source transcription is enabled but AIVF_ASR_MODEL_PATH is not set. "
            "Point it to a local faster-whisper/CTranslate2 model directory; model "
            "names and automatic network downloads are deliberately disabled."
        )
    path = Path(configured).expanduser()
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceTranscriptionError(
            "AIVF_ASR_MODEL_PATH must point to an existing local model directory"
        ) from exc
    if not path.is_dir() or not (path / "model.bin").is_file():
        raise SourceTranscriptionError(
            "AIVF_ASR_MODEL_PATH must be a local faster-whisper model directory "
            "containing model.bin"
        )
    return path


def _normalise_transcript_text(words: Sequence[str]) -> str:
    """Join Whisper word fragments without inserting spaces before punctuation."""
    result = ""
    closing = set(",.;:!?%)]}…")
    opening = set("([{“‘")
    for raw in words:
        word = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not word:
            continue
        if not result:
            result = word
        elif word[0] in closing or result[-1] in opening:
            result += word
        else:
            result += " " + word
    return result.strip()


def _format_srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000.0)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=".aivf-transcription-", suffix=".partial", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def transcribe_source(
    video_path: str | os.PathLike[str],
    *,
    model_path: str | os.PathLike[str] | None = None,
    language: str | None = None,
    duration_seconds: float | None = None,
    max_segments: int = 10_000,
    max_words: int = 200_000,
    max_text_chars: int = 1_000_000,
) -> dict[str, Any]:
    """Transcribe input audio with bounded, timestamp-validated local inference."""
    source = Path(video_path)
    if not source.is_file():
        raise SourceTranscriptionError("Source transcription requires a regular source video file")
    if max_segments < 1 or max_words < 1 or max_text_chars < 1:
        raise ValueError("transcription bounds must be positive integers")

    model_dir = _local_model_path(model_path)
    raw_language = (language if language is not None else os.environ.get("AIVF_ASR_LANGUAGE", "")).strip()
    selected_language = raw_language or None
    if selected_language and not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", selected_language):
        raise SourceTranscriptionError("AIVF_ASR_LANGUAGE must be a language code such as 'en' or 'nl'")

    device = os.environ.get("AIVF_ASR_DEVICE", "cpu").strip().lower()
    if device not in {"cpu", "cuda"}:
        raise SourceTranscriptionError("AIVF_ASR_DEVICE must be 'cpu' or 'cuda'")
    compute_type = os.environ.get("AIVF_ASR_COMPUTE_TYPE", "int8" if device == "cpu" else "float16").strip().lower()
    allowed_compute_types = {"int8", "int8_float16", "int8_bfloat16", "float16", "float32", "bfloat16"}
    if compute_type not in allowed_compute_types:
        raise SourceTranscriptionError(
            "AIVF_ASR_COMPUTE_TYPE must be one of: " + ", ".join(sorted(allowed_compute_types))
        )

    duration: float | None = None
    if duration_seconds is not None:
        duration = _finite_number(duration_seconds, "source duration")
        if duration <= 0:
            raise SourceTranscriptionError("Source transcription needs a positive source duration")

    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SourceTranscriptionError(
            "Source transcription was enabled but faster-whisper is not installed in this "
            "runtime. Install the optional ASR dependency and rebuild/restart the runtime."
        ) from exc

    try:
        # A filesystem path (not a Hugging Face model ID) keeps model resolution offline.
        model = WhisperModel(str(model_dir), device=device, compute_type=compute_type)
        generated, info = model.transcribe(
            str(source),
            language=selected_language,
            beam_size=5,
            word_timestamps=True,
            vad_filter=True,
        )
        segments_out: list[dict[str, Any]] = []
        word_count = 0
        total_chars = 0
        previous_start = -1.0
        for generated_segment in generated:
            if len(segments_out) >= max_segments:
                raise SourceTranscriptionError(
                    f"Transcription exceeded the {max_segments} segment safety limit"
                )
            start = _finite_number(getattr(generated_segment, "start", None), "segment start")
            end = _finite_number(getattr(generated_segment, "end", None), "segment end")
            text = re.sub(r"\s+", " ", str(getattr(generated_segment, "text", "") or "")).strip()
            if start < 0 or end <= start or start + 1e-6 < previous_start:
                raise SourceTranscriptionError("Transcription returned invalid or out-of-order segment times")
            if duration is not None:
                tolerance = max(0.25, min(1.0, duration * 0.01))
                if start >= duration + tolerance or end > duration + tolerance:
                    raise SourceTranscriptionError("Transcription timestamps exceed the source-video duration")
                start = min(start, duration)
                end = min(end, duration)
                if end <= start:
                    continue
            if not text:
                continue
            previous_start = start
            total_chars += len(text)
            if total_chars > max_text_chars:
                raise SourceTranscriptionError("Transcription exceeded the text-size safety limit")

            words_out: list[dict[str, Any]] = []
            for generated_word in (getattr(generated_segment, "words", None) or []):
                raw_word = str(getattr(generated_word, "word", "") or "")
                word_text = re.sub(r"\s+", " ", raw_word).strip()
                if not word_text:
                    continue
                try:
                    word_start = _finite_number(getattr(generated_word, "start", None), "word start")
                    word_end = _finite_number(getattr(generated_word, "end", None), "word end")
                except SourceTranscriptionError:
                    continue
                # Word alignment is optional evidence. Fall back to segment timing if
                # one word's alignment is malformed instead of poisoning the transcript.
                if word_start < start - 0.2 or word_end > end + 0.2 or word_end <= word_start:
                    continue
                word_start = max(start, word_start)
                word_end = min(end, word_end)
                if word_end <= word_start:
                    continue
                probability_value = getattr(generated_word, "probability", None)
                try:
                    probability = float(probability_value) if probability_value is not None else None
                except (TypeError, ValueError, OverflowError):
                    probability = None
                if probability is not None and not math.isfinite(probability):
                    probability = None
                words_out.append({
                    "text": word_text,
                    "start": round(word_start, 3),
                    "end": round(word_end, 3),
                    "probability": round(min(1.0, max(0.0, probability)), 4) if probability is not None else None,
                })
                word_count += 1
                if word_count > max_words:
                    raise SourceTranscriptionError(
                        f"Transcription exceeded the {max_words} word safety limit"
                    )
            segments_out.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "words": words_out,
            })

        language_value = str(getattr(info, "language", selected_language or "") or selected_language or "")
        probability_value = getattr(info, "language_probability", None)
        try:
            language_probability = float(probability_value) if probability_value is not None else None
        except (TypeError, ValueError, OverflowError):
            language_probability = None
        if language_probability is not None and not math.isfinite(language_probability):
            language_probability = None

        full_text = " ".join(item["text"] for item in segments_out).strip()
        if len(full_text) > max_text_chars:
            raise SourceTranscriptionError("Transcription exceeded the text-size safety limit")
        return {
            "schema_version": "1.0",
            "engine": "faster-whisper",
            "model_name": model_dir.name,
            "device": device,
            "compute_type": compute_type,
            "language": language_value,
            "language_probability": (
                round(min(1.0, max(0.0, language_probability)), 4)
                if language_probability is not None else None
            ),
            "duration_seconds": duration,
            "segment_count": len(segments_out),
            "word_count": word_count,
            "full_text": full_text,
            "segments": segments_out,
        }
    except SourceTranscriptionError:
        raise
    except Exception as exc:
        raise SourceTranscriptionError(
            f"Local source transcription failed ({type(exc).__name__}); verify the local model, "
            "source audio, and AIVF_ASR_DEVICE/AIVF_ASR_COMPUTE_TYPE settings"
        ) from exc


def apply_transcription_to_scenes(
    scenes: Sequence[Any],
    transcription: Mapping[str, Any],
) -> int:
    """Attach timestamped spoken-word evidence to overlapping scene records."""
    indexed = sorted(
        [(index, scene) for index, scene in enumerate(scenes)],
        key=lambda item: (float(item[1].start), float(item[1].end)),
    )
    if not indexed:
        return 0
    starts = [float(scene.start) for _, scene in indexed]
    words_by_scene: list[list[str]] = [[] for _ in indexed]
    for segment in transcription.get("segments", []):
        if not isinstance(segment, Mapping):
            continue
        timed_words = segment.get("words") or []
        if timed_words:
            candidates = [
                (str(word.get("text") or ""), (float(word.get("start", 0.0)) + float(word.get("end", 0.0))) / 2.0)
                for word in timed_words if isinstance(word, Mapping)
            ]
        else:
            candidates = [(
                str(segment.get("text") or ""),
                (float(segment.get("start", 0.0)) + float(segment.get("end", 0.0))) / 2.0,
            )]
        for word_text, midpoint in candidates:
            if not word_text.strip() or not math.isfinite(midpoint):
                continue
            found = bisect_right(starts, midpoint) - 1
            if found < 0 or midpoint > float(indexed[found][1].end):
                continue
            # Right-open intervals prevent a boundary word from being duplicated.
            if midpoint >= float(indexed[found][1].end):
                continue
            words_by_scene[found].append(word_text)

    changed = 0
    for position, (_original_index, scene) in enumerate(indexed):
        assigned = _normalise_transcript_text(words_by_scene[position])
        if assigned:
            scene.transcript = assigned
            changed += 1
    return changed


def write_transcription_artifacts(
    package_dir: str | os.PathLike[str],
    transcription: Mapping[str, Any],
) -> dict[str, str]:
    """Atomically write machine-readable transcript and SRT sidecar artifacts."""
    package = Path(package_dir)
    segments = transcription.get("segments", [])
    srt_blocks = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, Mapping):
            continue
        text = str(segment.get("text") or "").strip()
        try:
            start = _finite_number(segment.get("start"), "SRT start")
            end = _finite_number(segment.get("end"), "SRT end")
        except SourceTranscriptionError:
            continue
        if not text or start < 0 or end <= start:
            continue
        srt_blocks.append(
            f"{index}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}"
        )
    json_path = package / "source_transcription.json"
    srt_path = package / "source_transcript.srt"
    _atomic_write(json_path, json.dumps(dict(transcription), ensure_ascii=False, indent=2) + "\n")
    _atomic_write(srt_path, ("\n\n".join(srt_blocks) + "\n") if srt_blocks else "")
    return {"source_transcription": str(json_path), "source_transcript_srt": str(srt_path)}


__all__ = [
    "SourceTranscriptionError",
    "apply_transcription_to_scenes",
    "transcribe_source",
    "write_transcription_artifacts",
]
