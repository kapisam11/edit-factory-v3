from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ai_video_factory.media_health import (
    MediaHealthError,
    analyze_media,
    assert_render_quality,
    detect_black_frames,
    detect_silence,
    probe_media,
    stream_summary,
)


pytestmark = pytest.mark.integration


def _make_fixture(path: Path, *, audio: bool = True) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required for the media integration test")
    command = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=2",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac"]
    command += ["-shortest", str(path)]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        pytest.fail(f"fixture generation failed: {(completed.stderr or '')[-2000:]}")


def test_real_ffprobe_contract_and_fingerprint(tmp_path: Path) -> None:
    video = tmp_path / "fixture.mp4"
    _make_fixture(video)
    probe = probe_media(video)
    summary = stream_summary(probe)
    assert summary["has_video"] is True
    assert summary["has_audio"] is True
    assert summary["duration"] > 1.8
    report = analyze_media(video, deep=False)
    assert report["ok"] is True
    assert len(report["fingerprint"]["sha256"]) == 64


def test_real_media_filters_are_safe_on_clean_fixture(tmp_path: Path) -> None:
    video = tmp_path / "fixture.mp4"
    _make_fixture(video)
    assert detect_black_frames(video) == []
    assert detect_silence(video) == []


def test_render_contract_rejects_wrong_duration(tmp_path: Path) -> None:
    video = tmp_path / "fixture.mp4"
    _make_fixture(video)
    assert_render_quality(video, target_seconds=2.0, require_audio=True)
    with pytest.raises(MediaHealthError):
        assert_render_quality(video, target_seconds=5.0, require_audio=True)
