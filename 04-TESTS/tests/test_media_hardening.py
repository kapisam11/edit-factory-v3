import io
import json
import subprocess

import pytest

from ai_video_factory import complete_factory, music_intelligence
from ai_video_factory import music_mixer, quality_control_v3, render_engine, tts


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


def test_music_intelligence_duration_failure_is_not_coerced_to_zero(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "invalid media"

    monkeypatch.setattr(music_intelligence, "run_ffprobe", lambda *args, **kwargs: Result())
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        music_intelligence._duration("broken.mp3")


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


def test_quality_control_audio_analysis_failure_is_hard_failure(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")

    def failing_run(*args, **kwargs):
        raise RuntimeError("FFmpeg timed out")

    monkeypatch.setattr(quality_control_v3, "run_ffmpeg", failing_run)
    with pytest.raises(RuntimeError, match="Audio level analysis failed"):
        quality_control_v3.check_audio_levels(str(video))


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


def test_music_mixer_duration_failure_is_hard_failure(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    music = tmp_path / "music.mp3"
    output = tmp_path / "out.mp4"
    video.write_bytes(b"x")
    music.write_bytes(b"x")

    monkeypatch.setattr(music_mixer, "run_ffprobe", lambda *args, **kwargs: type("R", (), {
        "returncode": 1, "stdout": "", "stderr": "bad input"
    })())
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        music_mixer.add_music_to_video(str(video), str(music), str(output))


def test_render_engine_ffmpeg_failure_contains_stderr(monkeypatch):
    monkeypatch.setattr(render_engine.shutil, "which", lambda name: f"/usr/bin/{name}")

    def failing_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr="codec initialization failed")

    monkeypatch.setattr(render_engine.subprocess, "run", failing_run)
    with pytest.raises(RuntimeError, match="codec initialization failed"):
        render_engine.run_ffmpeg(["ffmpeg", "-version"], capture_output=True)


def test_render_engine_capture_disabled_streams_and_reports_ffmpeg_stderr(monkeypatch):
    monkeypatch.setattr(render_engine.shutil, "which", lambda name: f"/usr/bin/{name}")
    output = io.StringIO()
    monkeypatch.setattr(render_engine.sys, "stderr", output)
    stderr_data = "progress\nmissing encoder\n"
    stream = io.StringIO(stderr_data)

    class FakeProcess:
        stderr = stream

        def wait(self, timeout=None):
            assert timeout == 60
            return 1

    monkeypatch.setattr(render_engine.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    with pytest.raises(RuntimeError, match="missing encoder"):
        render_engine.run_ffmpeg(["ffmpeg", "-i", "input.mp4", "output.mp4"], timeout=60)
    assert output.getvalue() == stderr_data


def test_tts_output_validation_requires_audio_stream(monkeypatch, tmp_path):
    output = tmp_path / "voice.mp3"
    output.write_bytes(b"not-empty")

    class Result:
        returncode = 0
        stdout = json.dumps({"streams": [{"codec_type": "video", "duration": "2"}]})
        stderr = ""

    monkeypatch.setattr(tts, "run_ffprobe", lambda *args, **kwargs: Result())
    with pytest.raises(RuntimeError, match="does not contain an audio stream"):
        tts._validate_audio_output(str(output))


def test_render_engine_rejects_missing_media_inputs(tmp_path):
    with pytest.raises(FileNotFoundError, match="subtitle video"):
        render_engine.burn_subtitles(
            str(tmp_path / "missing.mp4"),
            str(tmp_path / "captions.srt"),
            str(tmp_path / "out.mp4"),
        )
    with pytest.raises(FileNotFoundError, match="voiceover audio"):
        render_engine.mix_voiceover(
            str(tmp_path / "video.mp4"),
            str(tmp_path / "missing.wav"),
            str(tmp_path / "out.mp4"),
        )
