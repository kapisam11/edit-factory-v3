from __future__ import annotations

import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from ai_video_factory.source_transcription import (
    SourceTranscriptionError,
    apply_transcription_to_scenes,
    transcribe_source,
    write_transcription_artifacts,
)
from ai_video_factory.production_models import Scene


def _model_dir(tmp_path: Path) -> Path:
    model = tmp_path / "local-whisper"
    model.mkdir()
    (model / "model.bin").write_bytes(b"model fixture")
    return model


def _fake_faster_whisper(monkeypatch, segments, info=None, *, failure=None):
    module = ModuleType("faster_whisper")

    class FakeWhisperModel:
        def __init__(self, model_size_or_path, *, device, compute_type):
            assert Path(model_size_or_path).is_dir()
            assert device == "cpu"
            assert compute_type == "int8"

        def transcribe(self, video_path, **kwargs):
            assert Path(video_path).is_file()
            assert kwargs["word_timestamps"] is True
            assert kwargs["vad_filter"] is True
            assert kwargs["language"] is None
            if failure:
                raise failure
            return iter(segments), info or SimpleNamespace(language="en", language_probability=0.98)

    module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)


def test_local_transcription_attaches_words_to_matching_scenes_and_writes_artifacts(
    monkeypatch, tmp_path
):
    model = _model_dir(tmp_path)
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fixture")
    generated = [
        SimpleNamespace(
            start=0.2,
            end=2.2,
            text=" The clutch starts now.",
            words=[
                SimpleNamespace(word=" The", start=0.2, end=0.45, probability=0.99),
                SimpleNamespace(word=" clutch", start=0.46, end=0.95, probability=0.98),
                SimpleNamespace(word=" starts", start=1.0, end=1.4, probability=0.97),
                SimpleNamespace(word=" now.", start=1.45, end=1.8, probability=0.96),
            ],
        ),
        SimpleNamespace(
            start=3.2,
            end=4.5,
            text=" He survives.",
            words=[
                SimpleNamespace(word=" He", start=3.2, end=3.5, probability=0.95),
                SimpleNamespace(word=" survives.", start=3.55, end=4.1, probability=0.94),
            ],
        ),
    ]
    _fake_faster_whisper(monkeypatch, generated)
    monkeypatch.setenv("AIVF_ASR_MODEL_PATH", str(model))
    monkeypatch.delenv("AIVF_ASR_LANGUAGE", raising=False)
    monkeypatch.delenv("AIVF_ASR_DEVICE", raising=False)
    monkeypatch.delenv("AIVF_ASR_COMPUTE_TYPE", raising=False)

    payload = transcribe_source(video, duration_seconds=5.0)
    scenes = [
        Scene("early", 0.0, 2.5),
        Scene("middle", 2.5, 3.0),
        Scene("late", 3.0, 5.0),
    ]
    changed = apply_transcription_to_scenes(scenes, payload)

    assert changed == 2
    assert scenes[0].transcript == "The clutch starts now."
    assert scenes[1].transcript == ""
    assert scenes[2].transcript == "He survives."
    assert payload["language"] == "en"
    assert payload["word_count"] == 6

    paths = write_transcription_artifacts(tmp_path / "package", payload)
    saved = json.loads(Path(paths["source_transcription"]).read_text(encoding="utf-8"))
    assert saved["full_text"] == "The clutch starts now. He survives."
    assert Path(paths["source_transcript_srt"]).read_text(encoding="utf-8").count(" --> ") == 2


def test_local_transcription_requires_a_real_model_directory_and_never_resolves_model_names(
    monkeypatch, tmp_path
):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fixture")
    monkeypatch.setenv("AIVF_ASR_MODEL_PATH", "large-v3")
    with pytest.raises(SourceTranscriptionError, match="existing local model directory"):
        transcribe_source(video)


def test_local_transcription_reports_missing_optional_package(monkeypatch, tmp_path):
    model = _model_dir(tmp_path)
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fixture")
    monkeypatch.setenv("AIVF_ASR_MODEL_PATH", str(model))
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(SourceTranscriptionError, match="faster-whisper is not installed"):
        transcribe_source(video)


def test_transcription_rejects_segment_timestamps_outside_source_duration(
    monkeypatch, tmp_path
):
    model = _model_dir(tmp_path)
    video = tmp_path / "source.mp4"
    video.write_bytes(b"fixture")
    generated = [
        SimpleNamespace(
            start=6.0,
            end=7.0,
            text="out of bounds",
            words=[],
        )
    ]
    _fake_faster_whisper(monkeypatch, generated)
    monkeypatch.setenv("AIVF_ASR_MODEL_PATH", str(model))
    monkeypatch.delenv("AIVF_ASR_LANGUAGE", raising=False)
    monkeypatch.delenv("AIVF_ASR_DEVICE", raising=False)
    monkeypatch.delenv("AIVF_ASR_COMPUTE_TYPE", raising=False)
    with pytest.raises(SourceTranscriptionError, match="exceed the source-video duration"):
        transcribe_source(video, duration_seconds=5.0)


def test_transcription_uses_segment_timing_when_word_timestamps_are_unavailable():
    scenes = [Scene("first", 0.0, 3.0), Scene("second", 3.0, 5.0)]
    payload = {
        "segments": [
            {"start": 0.5, "end": 1.8, "text": "This is the first scene", "words": []},
            {"start": 3.4, "end": 4.3, "text": "Final scene", "words": []},
        ]
    }
    assert apply_transcription_to_scenes(scenes, payload) == 2
    assert scenes[0].transcript == "This is the first scene"
    assert scenes[1].transcript == "Final scene"
