import subprocess
import shutil

import pytest


@pytest.mark.integration
def test_retention_verification_requires_effect_above_control(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe are required")

    from ai_video_factory.v3_quality import (
        enforce_retention_events,
        verify_retention_against_baseline,
    )

    baseline = tmp_path / "baseline.mp4"
    rendered = tmp_path / "rendered.mp4"
    subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "4", "-shortest", "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-c:a", "aac", str(baseline),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    enforce_retention_events(
        str(baseline),
        str(rendered),
        [{"time": 0.5, "kind": "zoom"}, {"time": 2.5, "kind": "motion"}],
    )
    report = verify_retention_against_baseline(
        str(baseline),
        str(rendered),
        [{"time": 0.5, "kind": "zoom"}, {"time": 2.5, "kind": "motion"}],
    )
    assert report["ok"] is True
    assert all(event["passed"] for event in report["events"])


def test_retention_visual_duration_ignores_container_audio_tail():
    from ai_video_factory.v3_quality import _visual_duration_seconds

    media = {"duration": 8.084, "video_duration": 8.0}
    assert _visual_duration_seconds(media) == 8.0


def test_retention_duration_tolerance_accounts_for_frame_rate_quantization():
    from ai_video_factory.v3_quality import _retention_duration_tolerance

    tolerance = _retention_duration_tolerance(
        {"fps": "24/1"},
        {"fps": "30/1"},
    )
    assert tolerance == pytest.approx((2.0 / 24.0) + 0.005)
    assert _retention_duration_tolerance(
        {"fps": "60/1"},
        {"fps": "60/1"},
    ) == pytest.approx(0.08)


def test_caption_retention_gate_compares_the_same_localized_region(monkeypatch):
    from ai_video_factory import v3_quality

    monkeypatch.setattr(
        v3_quality,
        "probe_media",
        lambda _path: {
            "width": 1080,
            "height": 1920,
            "duration": 4.0,
            "video_duration": 4.0,
            "fps": "30/1",
        },
    )

    baseline_frame = bytes([100]) * (160 * 90)

    def sample_frames(path, timestamps):
        frames = {}
        for timestamp in timestamps:
            frame = bytearray(baseline_frame)
            if path == "rendered.mp4":
                if timestamp <= 1.0:
                    # A measurable caption in the lower band at the retention event.
                    for row in range(int(90 * 0.68), 90):
                        start = row * 160
                        frame[start : start + 160] = bytes([102]) * 160
                else:
                    # Larger unrelated changes elsewhere in the frame must not
                    # drown out the smaller, semantically verified caption.
                    for row in range(0, int(90 * 0.68)):
                        start = row * 160
                        frame[start : start + 160] = bytes([104]) * 160
            frames[round(float(timestamp), 3)] = bytes(frame)
        return frames

    monkeypatch.setattr(v3_quality, "_sample_gray_frames", sample_frames)

    report = v3_quality.verify_retention_against_baseline(
        "baseline.mp4",
        "rendered.mp4",
        [{"time": 0.8, "kind": "caption"}],
    )

    assert report["ok"] is True
    assert report["events"][0]["passed"] is True
    assert report["events"][0]["semantic_verification"]["passed"] is True
    assert report["events"][0]["event_delta"] == pytest.approx(2.0)
    assert report["events"][0]["control_delta"] == pytest.approx(0.0)

