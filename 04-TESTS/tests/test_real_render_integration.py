import json
import shutil
import subprocess

import pytest

from ai_video_factory.production_pipeline import run_production_pipeline
from ai_video_factory.advanced_intelligence import WordTimestamp


@pytest.mark.integration
def test_production_pipeline_renders_real_mp4(tmp_path, monkeypatch):
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe are required for the real render integration test")

    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24",
            "-t", "8", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(source),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Keep this test offline/deterministic while still exercising the real media path.
    def fake_words(text, output_audio, **kwargs):
        subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "8", "-q:a", "9", "-acodec", "libmp3lame", output_audio],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        words = [WordTimestamp(word, i * 0.5, i * 0.5 + 0.4) for i, word in enumerate(text.split()[:12])]
        if kwargs.get("timestamps_path"):
            with open(kwargs["timestamps_path"], "w", encoding="utf-8") as handle:
                json.dump([word.__dict__ for word in words], handle)
        return words

    monkeypatch.setattr("ai_video_factory.advanced_intelligence.generate_word_timestamps", fake_words)
    monkeypatch.setattr("ai_video_factory.composer.generate_voiceover", lambda text, output_path: shutil.copyfile(output_audio_for_test(ffmpeg, tmp_path), output_path))

    result = run_production_pipeline(
        str(source),
        "integration render",
        str(tmp_path / "package"),
        target_seconds=15.0,
        enable_object_detection=False,
        enable_diarization=False,
        skip_qc=True,
    )

    assert result.errors == []
    assert result.final_video is not None
    assert result.final_video.endswith("final.mp4")
    assert shutil.which("ffprobe") is not None
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", result.final_video],
        check=True,
        capture_output=True,
        text=True,
    )
    assert float(probe.stdout.strip()) > 0.0


def output_audio_for_test(ffmpeg, tmp_path):
    path = tmp_path / "fallback_voice.mp3"
    if not path.exists():
        subprocess.run(
            [ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "8", "-q:a", "9", "-acodec", "libmp3lame", str(path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return str(path)
