import pytest

from ai_video_factory import render_engine


def test_validate_media_output_rejects_missing(tmp_path):
    with pytest.raises(RuntimeError):
        render_engine.validate_media_output(str(tmp_path / "missing.mp4"))


def test_write_concat_list_escapes_apostrophes(tmp_path):
    path = tmp_path / "list.txt"
    media = tmp_path / "a file's clip.mp4"
    media.write_bytes(b"placeholder")
    render_engine.write_concat_list([str(media)], str(path))
    text = path.read_text(encoding="utf-8")
    assert "a file" in text
    assert "'\\''" in text


def test_write_concat_list_rejects_missing_media(tmp_path):
    with pytest.raises(FileNotFoundError):
        render_engine.write_concat_list([str(tmp_path / "missing.mp4")], str(tmp_path / "list.txt"))


def test_run_ffmpeg_rejects_non_ffmpeg_command():
    with pytest.raises(ValueError):
        render_engine.run_ffmpeg(["echo", "hello"])


def test_apply_overlay_uses_argv_not_shell(monkeypatch, tmp_path):
    from ai_video_factory import templates

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))

    monkeypatch.setattr(templates.subprocess, "run", fake_run)
    input_clip = str(tmp_path / "clip; touch PWNED.mp4")
    overlay = str(tmp_path / "overlay.png")
    output = str(tmp_path / "out; touch PWNED.mp4")

    templates.apply_overlay(input_clip, overlay, output)

    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd[0] == "ffmpeg"
    assert input_clip in cmd
    assert overlay in cmd
    assert output in cmd
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 3600


def test_mix_voiceover_preserves_program_audio_and_duration(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    voice = tmp_path / "voice.mp3"
    output = tmp_path / "out.mp4"
    video.write_bytes(b"video")
    voice.write_bytes(b"voice")
    calls = []

    monkeypatch.setattr(
        render_engine,
        "validate_media_output",
        lambda path, **kwargs: (
            {"format": {"duration": "12.5"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
            if path == str(video)
            else {"format": {"duration": "12.5"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]}
        ),
    )
    monkeypatch.setattr(render_engine, "run_ffmpeg", lambda cmd: calls.append(cmd))

    render_engine.mix_voiceover(str(video), str(voice), str(output))

    assert calls
    cmd = calls[0]
    assert "-filter_complex" in cmd
    filter_index = cmd.index("-filter_complex")
    filter_text = cmd[filter_index + 1]
    assert "amix=inputs=2" in filter_text
    assert "-t" in cmd
    assert "12.500" in cmd
    assert "-shortest" not in cmd
