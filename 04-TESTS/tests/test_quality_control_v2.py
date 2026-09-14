from types import SimpleNamespace

from ai_video_factory import quality_control_v2


def test_black_frame_parser_accepts_ffmpeg_colon_format(monkeypatch, tmp_path):
    def fake_run_ffmpeg(cmd, capture_output=False):
        assert capture_output is True
        return SimpleNamespace(stderr="black_start:0.500 black_end:1.750")

    monkeypatch.setattr(quality_control_v2, "run_ffmpeg", fake_run_ffmpeg)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"placeholder")
    result = quality_control_v2.check_black_frames(str(video))

    assert result == [{"start": 0.5, "end": 1.75, "duration": 1.25}]


def test_frozen_frame_parser_accepts_ffmpeg_colon_format(monkeypatch, tmp_path):
    def fake_run_ffmpeg(cmd, capture_output=False):
        return SimpleNamespace(stderr="freeze_start:2.000 freeze_end:3.250")

    monkeypatch.setattr(quality_control_v2, "run_ffmpeg", fake_run_ffmpeg)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"placeholder")
    result = quality_control_v2.check_frozen_frames(str(video))

    assert result == [{"start": 2.0, "end": 3.25, "duration": 1.25}]
