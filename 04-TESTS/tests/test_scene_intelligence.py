import pytest

from ai_video_factory import scene_intelligence
from ai_video_factory.production_models import Scene


def test_scene_scoring_uses_relevance_and_importance():
    scene = Scene(
        id="s1", start=0.0, end=2.0,
        description="minecraft castle battle",
        objects=[], text=["castle"], motion_score=0.8,
        brightness=0.5, face_count=0, importance_score=0.9,
        source="clip.mp4",
    )
    score = scene_intelligence.score_scene(scene, "castle")
    assert score > 0.5


def test_save_and_load_scene_index(tmp_path):
    scene = Scene(
        id="s1", start=0.0, end=2.0, description="test",
        objects=[], text=[], motion_score=0.2,
        brightness=0.5, face_count=0, importance_score=0.3,
        source="clip.mp4",
    )
    path = tmp_path / "scenes.json"
    scene_intelligence.save_scene_index([scene], str(path), source_video="clip.mp4")
    loaded = scene_intelligence.load_scene_index(str(path))
    assert len(loaded) == 1
    assert loaded[0].id == "s1"
    assert loaded[0].source == "clip.mp4"


def test_audio_energy_parses_rms_from_ffmpeg(monkeypatch):
    class Result:
        stderr = "[Parsed_astats_0 @ 0x0] RMS level dB: -6.02\\n"

    monkeypatch.setattr(scene_intelligence, "run_ffmpeg", lambda *args, **kwargs: Result())
    energy = scene_intelligence._audio_energy("audio.mp4", 0.0, 2.0)
    assert 0.49 < energy < 0.51


def test_ffprobe_duration_failure_is_hard_failure(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "invalid media"

    monkeypatch.setattr(scene_intelligence, "run_ffprobe", lambda *args, **kwargs: Result())
    with pytest.raises(scene_intelligence.SceneAnalysisError, match="FFprobe failed"):
        scene_intelligence._ffprobe_duration("broken.mp4")


def test_ffprobe_duration_malformed_value_is_hard_failure(monkeypatch):
    class Result:
        returncode = 0
        stdout = "not-a-duration\n"
        stderr = ""

    monkeypatch.setattr(scene_intelligence, "run_ffprobe", lambda *args, **kwargs: Result())
    with pytest.raises(scene_intelligence.SceneAnalysisError, match="invalid duration"):
        scene_intelligence._ffprobe_duration("broken.mp4")


def test_ffprobe_duration_non_positive_value_is_hard_failure(monkeypatch):
    class Result:
        returncode = 0
        stdout = "0\n"
        stderr = ""

    monkeypatch.setattr(scene_intelligence, "run_ffprobe", lambda *args, **kwargs: Result())
    with pytest.raises(scene_intelligence.SceneAnalysisError, match="non-positive duration"):
        scene_intelligence._ffprobe_duration("empty.mp4")
