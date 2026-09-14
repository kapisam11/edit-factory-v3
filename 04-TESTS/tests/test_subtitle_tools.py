from ai_video_factory import subtitle_tools


def test_render_variants_uses_shared_ffmpeg_runner(monkeypatch, tmp_path):
    input_video = tmp_path / "input.mp4"
    srt_path = tmp_path / "subs.srt"
    input_video.write_bytes(b"video")
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")

    ffmpeg_calls = []
    validation_calls = []
    monkeypatch.setattr(subtitle_tools, "run_ffmpeg", lambda cmd: ffmpeg_calls.append(cmd))
    monkeypatch.setattr(
        subtitle_tools, "validate_media_output", lambda path: validation_calls.append(path)
    )

    result = subtitle_tools.render_variants(str(input_video), str(srt_path), str(tmp_path / "out"))

    assert len(result) == 3
    assert len(ffmpeg_calls) == 3
    assert len(validation_calls) == 3
    assert all(cmd[0] == "ffmpeg" for cmd in ffmpeg_calls)
