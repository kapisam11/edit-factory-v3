import io
import subprocess

import pytest

from ai_video_factory import render_engine
from ai_video_factory.production_guardrails import GuardrailError, ToolResult


def test_run_ffprobe_accepts_resolved_binary_path(monkeypatch):
    calls = {}

    def fake_tool(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return ToolResult(tuple(cmd), 0, "{}", "")

    monkeypatch.setattr(render_engine, "_ffprobe_binary", lambda: "/usr/bin/ffprobe")
    monkeypatch.setattr(render_engine, "run_tool", fake_tool)
    result = render_engine.run_ffprobe(["ffprobe", "-v", "error"], timeout=7)
    assert result.returncode == 0
    assert result.stdout == "{}"
    assert calls["cmd"][0] == "/usr/bin/ffprobe"
    assert calls["kwargs"]["timeout"] == 7
    assert calls["kwargs"]["max_stdout_bytes"] == 8 * 1024 * 1024


def test_run_ffmpeg_capture_path_accepts_resolved_binary_path(monkeypatch, tmp_path):
    calls = {}
    stdout = io.StringIO("ffmpeg version test\n")
    stderr = io.StringIO("")

    class FakeProcess:
        pid = 1234
        returncode = 0

        def __init__(self):
            self.stdout = stdout
            self.stderr = stderr

        def wait(self, timeout=None):
            calls["wait_timeout"] = timeout
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def fake_popen(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(render_engine.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(render_engine, "_ffmpeg_binary", lambda: "/usr/bin/ffmpeg")
    result = render_engine.run_ffmpeg(
        ["ffmpeg", "-y", str(tmp_path / "output.mp4")],
        timeout=9,
        capture_output=True,
    )

    assert calls["wait_timeout"] == 9
    assert calls["kwargs"]["stdout"] == render_engine.subprocess.PIPE
    assert calls["kwargs"]["stderr"] == render_engine.subprocess.PIPE
    assert calls["kwargs"]["text"] is True
    assert result.stdout == "ffmpeg version test\n"
    assert result.returncode == 0

def test_run_ffmpeg_streams_stderr_and_keeps_bounded_tail(monkeypatch):
    stderr_data = "progress 1\nprogress 2\ncodec initialization failed\n"
    stream = io.StringIO(stderr_data)
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

    assert output.getvalue() == stderr_data


def test_ffprobe_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(render_engine, "_ffprobe_binary", lambda: "ffprobe.exe")

    def fake_tool(*_args, **kwargs):
        raise GuardrailError(f"tool timed out after {kwargs['timeout']}s: ffprobe")

    monkeypatch.setattr(render_engine, "run_tool", fake_tool)
    with pytest.raises(RuntimeError, match="FFprobe timed out after 3s"):
        render_engine.run_ffprobe(["ffprobe", "-v", "error"], timeout=3)


def test_run_ffmpeg_capture_timeout_terminates_process_tree(monkeypatch, tmp_path):
    calls = {"waits": 0, "terminated": []}

    class FakeProcess:
        pid = 5678
        returncode = None

        def __init__(self):
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("stalled encoder\n")

        def wait(self, timeout=None):
            calls["waits"] += 1
            if calls["waits"] <= 2:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    process = FakeProcess()

    def fake_terminate(proc, *, grace_seconds=2.0):
        calls["terminated"].append((proc.pid, grace_seconds))

    monkeypatch.setattr(render_engine.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(render_engine, "_ffmpeg_binary", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(render_engine, "terminate_process_tree", fake_terminate)

    with pytest.raises(render_engine.FFmpegExecutionError, match="timed out after 1s"):
        render_engine.run_ffmpeg(
            ["ffmpeg", "-y", str(tmp_path / "output.mp4")],
            timeout=1,
            capture_output=True,
        )

    assert calls["terminated"]
    assert process.returncode == -9


def test_run_ffmpeg_capture_caps_stdout_and_terminates_tree(monkeypatch, tmp_path):
    calls = {"terminated": []}

    class FakeProcess:
        pid = 7890
        returncode = 0

        def __init__(self):
            self.stdout = io.StringIO("x" * 2048)
            self.stderr = io.StringIO("")

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    process = FakeProcess()

    def fake_terminate(proc, *, grace_seconds=2.0):
        calls["terminated"].append(proc.pid)
        proc.returncode = -9

    monkeypatch.setenv("AIVF_FFMPEG_CAPTURE_LIMIT_BYTES", "1024")
    monkeypatch.setattr(render_engine.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(render_engine, "_ffmpeg_binary", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(render_engine, "terminate_process_tree", fake_terminate)

    with pytest.raises(render_engine.FFmpegExecutionError, match="capture limit of 1024 bytes"):
        render_engine.run_ffmpeg(
            ["ffmpeg", "-y", str(tmp_path / "output.mp4")],
            timeout=9,
            capture_output=True,
        )
    assert calls["terminated"] == [7890]


def test_ffmpeg_budget_caps_captured_stdout_and_terminates_tree(monkeypatch):
    from ai_video_factory import ffmpeg_budget
    import contextlib
    import io

    calls = []

    class FakeProcess:
        pid = 8932
        returncode = 0

        def __init__(self, *args, **kwargs):
            self.stdout = io.BytesIO(b"x" * 2048)
            self.stderr = io.BytesIO(b"")

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def terminate(process, *, grace_seconds=2.0):
        calls.append(process.pid)
        process.returncode = -9

    monkeypatch.setenv("AIVF_FFMPEG_SUBPROCESS_CAPTURE_LIMIT_BYTES", "1024")
    monkeypatch.setattr(ffmpeg_budget.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(ffmpeg_budget, "slot", contextlib.nullcontext)
    monkeypatch.setattr("ai_video_factory.production_guardrails.terminate_process_tree", terminate)

    with pytest.raises(RuntimeError, match="capture limit of 1024 bytes"):
        ffmpeg_budget.run_ffmpeg_subprocess(
            ["ffmpeg", "-i", "input.mp4", "-f", "null", "-"],
            check=True,
            capture_output=True,
            timeout=5,
        )
    assert calls == [8932]


def test_ffmpeg_budget_timeout_terminates_process_tree(monkeypatch):
    from ai_video_factory import ffmpeg_budget
    import contextlib
    import io

    calls = {"waits": 0, "terminated": []}

    class FakeProcess:
        pid = 8933
        returncode = None

        def __init__(self, *args, **kwargs):
            self.stdout = io.BytesIO(b"partial")
            self.stderr = io.BytesIO(b"stalled")

        def wait(self, timeout=None):
            calls["waits"] += 1
            if calls["waits"] <= 2:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

    def terminate(process, *, grace_seconds=2.0):
        calls["terminated"].append(process.pid)

    monkeypatch.setattr(ffmpeg_budget.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(ffmpeg_budget, "slot", contextlib.nullcontext)
    monkeypatch.setattr("ai_video_factory.production_guardrails.terminate_process_tree", terminate)

    with pytest.raises(subprocess.TimeoutExpired):
        ffmpeg_budget.run_ffmpeg_subprocess(
            ["ffmpeg", "-i", "input.mp4", "-f", "null", "-"],
            check=True,
            capture_output=True,
            text=False,
            timeout=1,
        )
    assert calls["terminated"] == [8933]
