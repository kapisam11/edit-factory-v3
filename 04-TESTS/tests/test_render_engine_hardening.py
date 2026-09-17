import io
import subprocess

import pytest

from ai_video_factory import render_engine


def test_run_ffprobe_accepts_resolved_binary_path(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(render_engine.subprocess, "run", fake_run)
    result = render_engine.run_ffprobe(["/usr/bin/ffprobe", "-v", "error"], timeout=7)
    assert result.returncode == 0
    assert calls["kwargs"]["timeout"] == 7


def test_run_ffmpeg_capture_path_accepts_resolved_binary_path(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(render_engine.subprocess, "run", fake_run)
    render_engine.run_ffmpeg(["/usr/bin/ffmpeg", "-version"], timeout=9, capture_output=True)
    assert calls["kwargs"]["timeout"] == 9
    assert calls["kwargs"]["check"] is True


def test_run_ffmpeg_streams_stderr_and_keeps_bounded_tail(monkeypatch):
    stream = io.StringIO("progress 1\nprogress 2\ncodec initialization failed\n")
    output = io.StringIO()
    monkeypatch.setattr(render_engine.sys, "stderr", output)

    class FakeProcess:
        stderr = stream
        returncode = 1

        def wait(self, timeout=None):
            assert timeout == 11
            return self.returncode

    monkeypatch.setattr(render_engine.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(render_engine, "_ffmpeg_binary", lambda: "/usr/bin/ffmpeg")

    with pytest.raises(RuntimeError, match="codec initialization failed"):
        render_engine.run_ffmpeg(["ffmpeg", "-version"], timeout=11)

    assert output.getvalue() == stream.getvalue()


def test_ffprobe_timeout_is_bounded(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(render_engine.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="FFprobe timed out"):
        render_engine.run_ffprobe(["ffprobe", "-v", "error"], timeout=3)
