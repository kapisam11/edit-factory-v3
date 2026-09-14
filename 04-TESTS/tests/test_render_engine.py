import pytest

from ai_video_factory import render_engine


def test_validate_media_output_rejects_missing(tmp_path):
    with pytest.raises(RuntimeError):
        render_engine.validate_media_output(str(tmp_path / "missing.mp4"))


def test_write_concat_list_escapes_apostrophes(tmp_path):
    path = tmp_path / "list.txt"
    render_engine.write_concat_list(["/tmp/a file's clip.mp4"], str(path))
    text = path.read_text(encoding="utf-8")
    assert "a file" in text
    assert "'\\''" in text


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
