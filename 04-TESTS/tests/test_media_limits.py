from pathlib import Path

import pytest

from ai_video_factory.media_limits import MediaLimits, estimate_resource_budget, validate_output_probe


def _payload(duration="30", width="1920", height="1080", fps="30/1", channels=2):
    return {
        "streams": [{
            "codec_type": "video",
            "width": int(width),
            "height": int(height),
            "avg_frame_rate": fps,
            "r_frame_rate": fps,
            "codec_name": "h264",
        }, {
            "codec_type": "audio",
            "channels": channels,
            "codec_name": "aac",
        }],
        "format": {"duration": duration, "size": "1", "format_name": "mp4"},
    }


def test_resource_budget_is_deterministic():
    summary = {"duration": 30.0, "width": 1920, "height": 1080, "fps": 30.0, "size": 1000}
    assert estimate_resource_budget(summary) == estimate_resource_budget(summary)


def test_output_limit_rejects_large_duration(tmp_path):
    path = tmp_path / "output.mp4"
    path.write_bytes(b"x")
    with pytest.raises(Exception, match="duration"):
        validate_output_probe(path, _payload(duration="1000"), limits=MediaLimits(max_duration_seconds=60))


def test_output_limit_rejects_large_resolution(tmp_path):
    path = tmp_path / "output.mp4"
    path.write_bytes(b"x")
    with pytest.raises(Exception, match="resolution"):
        validate_output_probe(path, _payload(width="7680", height="4320"), limits=MediaLimits(max_width=3840, max_height=2160))


def test_output_limit_rejects_excess_channels(tmp_path):
    path = tmp_path / "output.mp4"
    path.write_bytes(b"x")
    with pytest.raises(Exception, match="channel"):
        validate_output_probe(path, _payload(channels=16), limits=MediaLimits(max_audio_channels=8))


def test_input_size_limit_rejects_before_probe(tmp_path):
    from ai_video_factory.media_limits import validate_input_file

    path = tmp_path / "input.mp4"
    path.write_bytes(b"not-a-real-video")
    with pytest.raises(Exception, match="size"):
        validate_input_file(path, suffix=".mp4", limits=MediaLimits(max_input_bytes=4))


def test_input_signature_rejects_mismatched_container(tmp_path):
    from ai_video_factory.media_limits import validate_input_file

    path = tmp_path / "input.mp4"
    path.write_bytes(b"plain text")
    with pytest.raises(Exception, match="signature"):
        validate_input_file(path, suffix=".mp4", limits=MediaLimits())


def test_output_frame_limit_rejects_render_bomb(tmp_path):
    path = tmp_path / "output.mp4"
    path.write_bytes(b"x")
    payload = _payload()
    payload["streams"][0]["nb_frames"] = 200
    with pytest.raises(Exception, match="frame count"):
        validate_output_probe(path, payload, limits=MediaLimits(max_frames=100))


def test_output_codec_allowlist(tmp_path):
    path = tmp_path / "output.mp4"
    path.write_bytes(b"x")
    payload = _payload()
    payload["streams"][0]["codec_name"] = "unsupported_codec"
    with pytest.raises(Exception, match="video codec"):
        validate_output_probe(path, payload)


def test_output_bitrate_limit(tmp_path):
    path = tmp_path / "output.mp4"
    path.write_bytes(b"x")
    payload = _payload()
    payload["format"]["bit_rate"] = "200000000"
    with pytest.raises(Exception, match="bitrate"):
        validate_output_probe(path, payload)
