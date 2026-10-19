import json
from pathlib import Path

import pytest

import ai_video_factory.complete_factory as complete_factory
from ai_video_factory.complete_factory import (
    PLATFORM_LAYOUTS,
    _clip_window_candidates,
    _resolve_clip_source,
    _scene_crop_subjects,
    _scene_rows,
    render_all_platforms,
    render_clip_factory,
)


def test_platform_layouts_cover_requested_formats():
    assert PLATFORM_LAYOUTS["youtube_shorts"] == (1080, 1920)
    assert PLATFORM_LAYOUTS["youtube"] == (1920, 1080)
    assert PLATFORM_LAYOUTS["square"] == (1080, 1080)


def test_runtime_module_is_importable():
    from ai_video_factory.complete_factory import run_complete_factory, run_autonomous_queue

    assert callable(run_complete_factory)
    assert callable(run_autonomous_queue)


def test_render_all_platforms_requires_real_ffmpeg_input(tmp_path: Path):
    missing = tmp_path / "missing.mp4"
    try:
        render_all_platforms(str(missing), str(tmp_path), ["square"])
    except Exception as exc:
        assert "ffmpeg" in str(exc).lower() or "no such file" in str(exc).lower() or "failed" in str(exc).lower()
    else:
        raise AssertionError("rendering a missing source should fail")



def test_clip_candidates_use_original_source_and_diverse_windows(tmp_path: Path):
    original = tmp_path / "original-long-video.mp4"
    rendered_short = tmp_path / "rendered-short.mp4"
    original.write_bytes(b"source")
    rendered_short.write_bytes(b"short")

    assert _resolve_clip_source(str(original), str(rendered_short)) == str(original.resolve())
    assert _resolve_clip_source(None, str(rendered_short)) == str(rendered_short.resolve())
    with pytest.raises(FileNotFoundError, match="original input video"):
        _resolve_clip_source(str(tmp_path / "missing-original.mp4"), str(rendered_short))

    scenes = [
        {"id": "scene-a", "start": 80.0, "end": 82.0},
        {"id": "scene-b", "start": 91.0, "end": 94.0},
        {"id": "scene-c", "start": 250.0, "end": 255.0},
        {"id": "scene-d", "start": 470.0, "end": 475.0},
    ]
    scored = [
        {"shot_id": "scene-a", "score": 0.99},
        {"shot_id": "scene-b", "score": 0.98},
        {"shot_id": "scene-c", "score": 0.95},
        {"shot_id": "scene-d", "score": 0.90},
    ]
    windows = _clip_window_candidates(scenes, scored, count=3, duration=600.0)

    assert len(windows) == 3
    assert windows[0][0] < windows[1][0] < windows[2][0]
    assert all(start >= 0 and length > 0 and start + length <= 600.0 for start, length in windows)
    for index, (start, length) in enumerate(windows):
        for other_start, other_length in windows[index + 1:]:
            overlap = max(0.0, min(start + length, other_start + other_length) - max(start, other_start))
            assert overlap / min(length, other_length) <= 0.55


def test_clip_factory_writes_source_timestamps_without_duplicate_padding(tmp_path: Path, monkeypatch):
    source = tmp_path / "original-long-video.mp4"
    source.write_bytes(b"placeholder")
    package_dir = tmp_path / "package"
    commands = []

    monkeypatch.setattr(complete_factory, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(complete_factory, "_probe", lambda path: {"format": {"duration": "600"}})
    monkeypatch.setattr(complete_factory, "_run", lambda command: commands.append(command))
    monkeypatch.setattr(
        complete_factory,
        "_validate_output",
        lambda *args, **kwargs: {"ok": True, "errors": []},
    )

    outputs = render_clip_factory(
        str(source),
        str(package_dir),
        count=5,
        windows=[(100.0, 30.0), (105.0, 30.0), (300.0, 25.0)],
    )

    assert len(outputs) == 2
    assert all(str(source) in command for command in commands)
    report = json.loads((package_dir / "clip_candidates.json").read_text(encoding="utf-8"))
    assert report["source_video"] == str(source.resolve())
    assert report["selection_method"] == "scene-ranked-diverse"
    assert report["candidate_count"] == 2
    assert [item["source_start_seconds"] for item in report["candidates"]] == [100.0, 300.0]
    assert all(item["source_end_seconds"] <= 600.0 for item in report["candidates"])
    assert all("not_calibrated_to_views_or_retention" == item["selection_score_semantics"] for item in report["candidates"])



def test_clip_factory_fallback_does_not_pad_with_near_duplicates(tmp_path: Path, monkeypatch):
    source = tmp_path / "short-source.mp4"
    source.write_bytes(b"placeholder")
    package_dir = tmp_path / "package"

    monkeypatch.setattr(complete_factory, "_ffmpeg_available", lambda: True)
    monkeypatch.setattr(complete_factory, "_probe", lambda path: {"format": {"duration": "20"}})
    monkeypatch.setattr(complete_factory, "_run", lambda command: None)
    monkeypatch.setattr(
        complete_factory,
        "_validate_output",
        lambda *args, **kwargs: {"ok": True, "errors": []},
    )

    outputs = render_clip_factory(str(source), str(package_dir), count=5)
    report = json.loads((package_dir / "clip_candidates.json").read_text(encoding="utf-8"))

    assert len(outputs) == report["candidate_count"] == 2
    assert [item["source_start_seconds"] for item in report["candidates"]] == [0.0, 6.0]



def test_versioned_scene_index_is_loaded_for_candidate_selection():
    versioned = {
        "version": 1,
        "source_video": "original.mp4",
        "scene_count": 2,
        "scenes": [
            {"id": "scene-1", "start": 0.0, "end": 3.0},
            {"id": "scene-2", "start": 8.0, "end": 12.0},
            "malformed-row",
        ],
    }

    assert [row["id"] for row in _scene_rows(versioned)] == ["scene-1", "scene-2"]
    assert [row["id"] for row in _scene_rows([{"id": "legacy"}])] == ["legacy"]
    assert _scene_rows({"scenes": "not-a-list"}) == []


def test_crop_planner_ignores_object_labels_and_invalid_face_coordinates():
    assert _scene_crop_subjects({"objects": ["person", "car"]}) == []
    assert _scene_crop_subjects({"faces": [{"x": 1200, "y": 650}]}) == []
    assert _scene_crop_subjects({
        "faces": [
            {"x": 0.72, "y": 0.38},
            {"x": "nan", "y": 0.4},
            "person",
        ],
    }) == [{"x": 0.72, "y": 0.38}]
