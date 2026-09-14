from types import SimpleNamespace

from ai_video_factory import edit_automation


def test_duration_uses_shared_ffprobe_runner(monkeypatch):
    calls = []

    def fake_run_ffprobe(cmd):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="4.25\n")

    monkeypatch.setattr(edit_automation.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(edit_automation, "run_ffprobe", fake_run_ffprobe)

    assert edit_automation._get_duration("clip.mp4") == 4.25
    assert calls[0][0] == "/bin/ffprobe"


def test_trim_segment_uses_shared_ffmpeg_runner_and_validates(monkeypatch):
    calls = []
    monkeypatch.setattr(edit_automation, "run_ffmpeg", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(
        edit_automation,
        "validate_media_output",
        lambda path: calls.append(["validate", path]),
    )

    output = edit_automation.trim_segment("input.mp4", 1, 3, "out.mp4")

    assert output == "out.mp4"
    assert calls[0][0] == "ffmpeg"
    assert calls[1] == ["validate", "out.mp4"]
