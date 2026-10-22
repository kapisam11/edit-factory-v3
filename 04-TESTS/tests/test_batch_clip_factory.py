from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_video_factory import batch_clip_factory as batch
from ai_video_factory.production_models import ProductionResult, Scene


def _scene(
    scene_id: str,
    start: float,
    end: float,
    *,
    importance: float = 0.0,
    motion: float = 0.0,
    audio: float = 0.0,
    faces: int = 0,
    transcript: str = "",
) -> Scene:
    return Scene(
        id=scene_id,
        start=start,
        end=end,
        description=f"Scene {scene_id}",
        transcript=transcript,
        importance_score=importance,
        motion_score=motion,
        audio_energy=audio,
        face_count=faces,
    )


def test_select_clip_candidates_ranks_moments_and_avoids_overlap() -> None:
    scenes = [
        _scene("opening", 0, 5, importance=0.2, transcript="quiet intro"),
        _scene("action", 15, 20, importance=0.95, motion=0.9, audio=0.8, faces=2, transcript="big unexpected action"),
        _scene("middle", 35, 40, importance=0.3, motion=0.2, transcript="ordinary moment"),
        _scene("payoff", 55, 60, importance=0.9, motion=0.7, audio=0.9, faces=1, transcript="final surprising payoff"),
        _scene("ending", 75, 79, importance=0.1),
    ]

    candidates = batch.select_clip_candidates(
        scenes, 80, clip_seconds=20, count=5, min_gap_seconds=1
    )

    assert 1 <= len(candidates) <= 3
    assert candidates[0].anchor_scene_id in {"action", "payoff"}
    assert candidates[0].score > 50
    assert candidates[0].reasons
    assert all(candidate.duration == pytest.approx(20) for candidate in candidates)
    assert all(
        left.end + 1 <= right.start or right.end + 1 <= left.start
        for index, left in enumerate(candidates)
        for right in candidates[index + 1 :]
    )


@pytest.mark.parametrize(
    ("duration", "clip_seconds", "count", "gap"),
    [
        (7.9, 10, 2, 1),
        (30, 7, 2, 1),
        (30, 181, 2, 1),
        (30, 10, 0, 1),
        (30, 10, 11, 1),
        (30, 10, 2, -1),
    ],
)
def test_select_clip_candidates_rejects_invalid_bounds(
    duration: float, clip_seconds: float, count: int, gap: float
) -> None:
    with pytest.raises(batch.BatchClipError):
        batch.select_clip_candidates(
            [_scene("only", 0, max(duration, 1))],
            duration,
            clip_seconds=clip_seconds,
            count=count,
            min_gap_seconds=gap,
        )


def test_batch_factory_keeps_candidates_isolated_and_preserves_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "long-source.mp4"
    source.write_bytes(b"original-source-bytes")
    output = tmp_path / "batch-output"
    scenes = [
        _scene("first", 2, 6, importance=0.7, motion=0.7, transcript="first possible moment"),
        _scene("second", 22, 26, importance=0.99, motion=0.9, audio=0.9, transcript="best possible moment"),
        _scene("third", 36, 39, importance=0.1),
    ]
    monkeypatch.setattr(
        batch,
        "extract_media_metadata",
        lambda _path: {
            "format": {"duration": "40.0"},
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        },
    )
    monkeypatch.setattr(batch, "analyze_video", lambda *args, **kwargs: scenes)

    def fake_extract(_source, destination, *, start, duration):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"segment:{start}:{duration}".encode())
        return {"format": {"duration": str(duration)}}

    monkeypatch.setattr(batch, "_extract_segment", fake_extract)
    monkeypatch.setattr(
        batch,
        "validate_media_output",
        lambda _path, **kwargs: {
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
            "format": {"duration": "10.0"},
        },
    )
    calls: list[str] = []

    def fake_pipeline(input_video, topic, package_dir, **kwargs):
        calls.append(str(input_video))
        package = Path(package_dir)
        package.mkdir(parents=True, exist_ok=True)
        if "moment 01" in topic:
            return ProductionResult(package_dir=str(package), errors=["simulated candidate render failure"])
        final = package / "final.v3.mp4"
        final.write_bytes(b"independent-output")
        return ProductionResult(package_dir=str(package), final_video=str(final))

    monkeypatch.setattr(batch, "run_v3_pipeline", fake_pipeline)

    report = batch.run_batch_clip_factory(
        source,
        "A source-video story",
        output,
        count=2,
        clip_seconds=10,
    )

    assert report["status"] == "partial"
    assert report["summary"] == {"successful": 1, "failed": 1, "skipped": 0}
    assert len(calls) == 2
    assert source.read_bytes() == b"original-source-bytes"
    assert json.loads((output / "batch_manifest.json").read_text(encoding="utf-8"))["status"] == "partial"
    assert any(item["status"] == "succeeded" for item in report["candidates"])
    failed = next(item for item in report["candidates"] if item["status"] == "failed")
    assert "simulated candidate render failure" in failed["error"]


def test_batch_factory_refuses_nonempty_output_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    output = tmp_path / "existing-output"
    output.mkdir()
    (output / "keep.txt").write_text("do not touch", encoding="utf-8")

    with pytest.raises(batch.BatchClipError, match="new or empty"):
        batch.run_batch_clip_factory(source, "test topic", output)

    assert (output / "keep.txt").read_text(encoding="utf-8") == "do not touch"

@pytest.mark.integration
def test_extract_segment_creates_valid_media_without_touching_source(tmp_path: Path) -> None:
    from ai_video_factory.production_guardrails import sha256_file
    from ai_video_factory.render_engine import run_ffmpeg, validate_media_output

    source = tmp_path / "source.mp4"
    destination = tmp_path / "segment.mp4"
    run_ffmpeg(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "12",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        timeout=90,
    )
    source_hash = sha256_file(source)

    report = batch._extract_segment(source, destination, start=2.0, duration=8.0)

    assert destination.is_file()
    assert report["format"]["duration"]
    assert 7.5 <= float(report["format"]["duration"]) <= 8.5
    assert sha256_file(source) == source_hash
    assert not list(tmp_path.glob("*.partial.mp4"))

