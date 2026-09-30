import json
from pathlib import Path
from subprocess import CompletedProcess

from ai_video_factory.cache_lifecycle import CacheLifecycle, content_hash
from ai_video_factory.hardware import ffmpeg_preset_for
from ai_video_factory.media_metadata import extract_media_metadata
from ai_video_factory.observability_metrics import MetricsRegistry
from ai_video_factory import render_engine


def test_media_metadata_normalizes_ffprobe_output(monkeypatch, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fixture")

    payload = {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "12.5",
            "size": "12345",
            "tags": {
                "encoder": "Lavf",
                "location": "secret location",
            },
        },
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "pix_fmt": "yuv420p",
                "avg_frame_rate": "30000/1001",
                "color_space": "bt709",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
    }

    def fake_probe(*args, **kwargs):
        return CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("ai_video_factory.media_metadata.run_ffprobe", fake_probe)
    result = extract_media_metadata(media)

    assert result["format"]["duration"] == "12.5"
    assert result["streams"][0]["fps"] == 29.97003
    assert result["streams"][1]["sample_rate"] == "48000"
    assert "location" not in result["format"]["tags"]


def test_media_metadata_can_include_explicit_sensitive_tags(monkeypatch, tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"fixture")
    payload = {
        "format": {"tags": {"location": "51.0,5.0"}},
        "streams": [],
    }

    def fake_probe(*args, **kwargs):
        return CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("ai_video_factory.media_metadata.run_ffprobe", fake_probe)
    result = extract_media_metadata(media, include_sensitive=True)
    assert result["tags"]["location"] == "51.0,5.0"


def test_content_hash_is_stable_and_changes_with_content(tmp_path):
    path = tmp_path / "input.bin"
    path.write_bytes(b"one")
    first = content_hash(path)
    assert first == content_hash(path)
    path.write_bytes(b"two")
    assert first != content_hash(path)


def test_prometheus_export_has_valid_metric_name():
    registry = MetricsRegistry()
    registry.increment("http_requests_total:GET:/api/health", 2)
    registry.observe_ms("http_request", 12.5)
    text = registry.to_prometheus()
    assert "aivf_http_requests_total" in text
    assert 'method="GET"' in text
    assert 'endpoint="/api/health"' in text
    assert "aivf_http_request_milliseconds_avg 12.5" in text


def test_vaapi_preset_is_explicit():
    preset = ffmpeg_preset_for("h264_vaapi")
    assert preset["codec"] == "h264_vaapi"


def test_cache_lifecycle_still_enforces_size(tmp_path):
    cache = CacheLifecycle(tmp_path / "cache", max_bytes=10, ttl_seconds=60)
    (cache.root / "a.tmp").write_bytes(b"12345678901")
    assert cache.enforce_size() == 1


def test_resource_snapshot_is_json_serializable(monkeypatch):
    from ai_video_factory.resource_metrics import resource_json, snapshot_resources, summarize_resources

    monkeypatch.setattr("ai_video_factory.resource_metrics.shutil.which", lambda name: None)
    start = snapshot_resources()
    end = snapshot_resources()
    report = summarize_resources(start, end)
    assert report["elapsed_seconds"] >= 0
    assert "rss_start_bytes" in report
    assert "cpu_time_seconds_delta" in report
    assert resource_json(start, end).startswith("{")


def test_media_version_stamp_uses_copy_mode(monkeypatch, tmp_path):
    source = tmp_path / "final.mp4"
    source.write_bytes(b"source")
    calls = []

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"stamped")

    monkeypatch.setattr(render_engine, "run_ffmpeg", fake_run)
    monkeypatch.setattr(render_engine, "validate_media_output", lambda *args, **kwargs: {})
    result = render_engine.stamp_media_metadata(str(source), version="3.0.0-test")

    assert result == str(source)
    assert source.read_bytes() == b"stamped"
    assert "-c" in calls[0] and "copy" in calls[0]
    assert "comment=Edit Factory 3.0.0-test" in calls[0]
