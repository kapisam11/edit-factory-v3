import json
import subprocess

from ai_video_factory import complete_factory, music_intelligence
from ai_video_factory import music_mixer, quality_control_v3


def test_music_intelligence_duration_uses_hardened_ffprobe(monkeypatch):
    calls = {}

    class Result:
        returncode = 0
        stdout = "12.5\n"
        stderr = ""

    def fake_probe(cmd, timeout=None):
        calls["cmd"] = cmd
        calls["timeout"] = timeout
        return Result()

    monkeypatch.setattr(music_intelligence, "run_ffprobe", fake_probe)
    assert music_intelligence._duration("clip.mp4") == 12.5
    assert calls["timeout"] == 20
    assert calls["cmd"][0] == "ffprobe"


def test_quality_control_audio_analysis_has_bounded_runner(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    calls = []

    class Result:
        returncode = 0
        stderr = " loudnorm output {\"input_i\":\"-14.0\",\"input_tp\":\"-1.0\",\"input_lra\":\"4.0\"} "
        stdout = ""

    def fake_run(cmd, timeout=None, capture_output=False):
        calls.append((cmd, timeout, capture_output))
        return Result()

    monkeypatch.setattr(quality_control_v3, "run_ffmpeg", fake_run)
    report = quality_control_v3.check_audio_levels(str(video))
    assert report["integrated_lufs"] == "-14.0"
    assert len(calls) == 2
    assert all(timeout == 300 for _, timeout, _ in calls)


def test_complete_factory_probe_uses_hardened_ffprobe(monkeypatch):
    class Result:
        returncode = 0
        stdout = json.dumps({"format": {"duration": "9.5"}})
        stderr = ""

    calls = {}

    def fake_probe(cmd, timeout=None):
        calls["cmd"] = cmd
        calls["timeout"] = timeout
        return Result()

    monkeypatch.setattr(complete_factory, "run_ffprobe", fake_probe)
    result = complete_factory._probe("video.mp4")
    assert result["format"]["duration"] == "9.5"
    assert calls["timeout"] == 20
    assert calls["cmd"][0] == "ffprobe"


def test_music_mix_validates_final_media(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    music = tmp_path / "music.mp3"
    output = tmp_path / "out.mp4"
    video.write_bytes(b"x")
    music.write_bytes(b"x")
    calls = {"validated": False}

    monkeypatch.setattr(music_mixer, "run_ffmpeg", lambda cmd, **kwargs: None)

    def fake_validate(path, **kwargs):
        assert path == str(output)
        assert kwargs["require_video"] is True
        assert kwargs["require_audio"] is True
        calls["validated"] = True
        return {}

    monkeypatch.setattr(music_mixer, "validate_media_output", fake_validate)
    result = music_mixer.mix_audio(str(video), str(music), None, str(output))
    assert result == str(output)
    assert calls["validated"] is True
