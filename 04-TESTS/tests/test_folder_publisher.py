from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_video_factory import folder_publisher as folder


def _probe(_path: Path, *, max_duration_seconds: int) -> dict[str, object]:
    assert max_duration_seconds > 0
    return {
        "duration_seconds": 14.25,
        "width": 1080,
        "height": 1920,
        "video_codec": "h264",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "audio_streams": 1,
    }


def _publisher(tmp_path: Path, **overrides: object) -> folder.FolderPublisher:
    values: dict[str, object] = {
        "watch_dir": tmp_path / "incoming",
        "db_path": tmp_path / "state" / "folder.sqlite",
        "settle_seconds": 0,
        "poll_seconds": 1,
        "max_videos_per_day": 2,
        "publish_enabled": True,
        "privacy_status": "private",
        "allow_public": False,
    }
    values.update(overrides)
    return folder.FolderPublisher(folder.FolderPublisherConfig(**values))  # type: ignore[arg-type]


def test_probe_video_reports_average_frame_rate(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    monkeypatch.setenv("AIVF_MAX_INPUT_FPS", "60")
    monkeypatch.setattr(
        folder.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps({
                "streams": [{
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "avg_frame_rate": "30000/1001",
                    "r_frame_rate": "30/1",
                }],
                "format": {"duration": "12.0", "format_name": "mp4"},
            })
        ),
    )
    result = folder.probe_video(tmp_path / "clip.mp4", max_duration_seconds=30)
    assert result["frame_rate_fps"] == pytest.approx(29.97002997)


def test_probe_video_rejects_frame_rate_above_configured_limit(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    monkeypatch.setenv("AIVF_MAX_INPUT_FPS", "60")
    monkeypatch.setattr(
        folder.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps({
                "streams": [{
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "avg_frame_rate": "120/1",
                    "r_frame_rate": "120/1",
                }],
                "format": {"duration": "12.0", "format_name": "mp4"},
            })
        ),
    )
    with pytest.raises(ValueError, match="exceeds the configured 60-fps limit"):
        folder.probe_video(tmp_path / "clip.mp4", max_duration_seconds=30)


def test_probe_video_rejects_missing_or_invalid_frame_rate(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        folder.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=json.dumps({
                "streams": [{
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "avg_frame_rate": "0/0",
                    "r_frame_rate": "0/0",
                }],
                "format": {"duration": "12.0", "format_name": "mp4"},
            })
        ),
    )
    with pytest.raises(ValueError, match="frame rate"):
        folder.probe_video(tmp_path / "clip.mp4", max_duration_seconds=30)


def test_folder_scan_waits_for_explicit_approval_and_publishes_idempotently(
    tmp_path: Path, monkeypatch
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    video = incoming / "my_clip.mp4"
    video.write_bytes(b"stable fake media for test")
    monkeypatch.setattr(folder, "probe_video", _probe)
    publisher = _publisher(tmp_path)

    first = publisher.scan()
    assert first["observed_files"] == 1
    item = publisher.store.items()[0]
    assert item["state"] == "NEEDS_REVIEW"
    assert "sidecar missing" in item["last_error"]
    assert publisher.publish_one() is None

    sidecar = folder.approve_sidecar(
        incoming,
        video.name,
        ai_generated=False,
        realistic_alteration=False,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["publish_approved"] is True
    assert payload["rights_reviewed"] is True
    assert payload["ai_generated"] is False
    assert payload["realistic_alteration"] is False

    publisher.scan()
    ready = publisher.store.items()[0]
    assert ready["state"] == "READY"

    calls: list[dict[str, object]] = []
    uploaded_bytes: list[bytes] = []

    def fake_upload(path: str, **kwargs: object) -> dict[str, object]:
        calls.append({"path": path, **kwargs})
        uploaded_bytes.append(Path(path).read_bytes())
        return {"video_id": "test-video-id", "url": "https://youtube.com/watch?v=test-video-id"}

    monkeypatch.setattr(folder, "upload_video", fake_upload)
    result = publisher.publish_one()
    assert result == {
        "id": ready["id"],
        "state": "PUBLISHED",
        "video_id": "test-video-id",
        "url": "https://youtube.com/watch?v=test-video-id",
        "deduplicated": False,
    }
    assert len(calls) == 1
    assert calls[0]["privacy_status"] == "private"
    assert calls[0]["idempotency_key"].startswith("folder-v2-")
    assert Path(str(calls[0]["path"])) != video
    assert Path(str(calls[0]["path"])).parent == publisher.staging_dir.resolve()
    assert uploaded_bytes == [b"stable fake media for test"]

    # A later scan and daemon tick must not enqueue or publish this file again.
    publisher.scan()
    assert publisher.publish_one() is None
    assert publisher.store.items()[0]["state"] == "PUBLISHED"
    assert len(calls) == 1


def test_folder_watcher_does_not_assume_disclosure_type_for_new_files(
    tmp_path: Path, monkeypatch
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    video = incoming / "ai_clip.mp4"
    video.write_bytes(b"stable fake media for test")
    monkeypatch.setattr(folder, "probe_video", _probe)
    publisher = _publisher(tmp_path)

    folder.approve_sidecar(incoming, video.name)
    publisher.scan()
    item = publisher.store.items()[0]
    assert item["state"] == "NEEDS_REVIEW"
    assert "ai_generated must be explicitly" in item["last_error"]
    assert publisher.publish_one() is None


def test_folder_queue_deduplicates_identical_content(tmp_path: Path, monkeypatch) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    first = incoming / "one.mp4"
    second = incoming / "two.mp4"
    first.write_bytes(b"same content")
    second.write_bytes(b"same content")
    monkeypatch.setattr(folder, "probe_video", _probe)
    publisher = _publisher(tmp_path)

    folder.approve_sidecar(incoming, first.name, ai_generated=False, realistic_alteration=False)
    folder.approve_sidecar(incoming, second.name, ai_generated=False, realistic_alteration=False)
    publisher.scan()
    items = publisher.store.items()
    states = {item["path"]: item["state"] for item in items}
    assert sorted(states.values()) == ["DUPLICATE", "READY"]


def test_public_upload_requires_an_explicit_opt_in(tmp_path: Path) -> None:
    try:
        _publisher(tmp_path, privacy_status="public", allow_public=False)
    except ValueError as exc:
        assert "public upload is blocked" in str(exc)
    else:
        raise AssertionError("public publishing must fail closed by default")


def test_source_change_after_review_blocks_first_upload(tmp_path: Path, monkeypatch) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    video = incoming / "changing.mp4"
    video.write_bytes(b"first version")
    monkeypatch.setattr(folder, "probe_video", _probe)
    publisher = _publisher(tmp_path)
    folder.approve_sidecar(incoming, video.name, ai_generated=False, realistic_alteration=False)
    publisher.scan()
    assert publisher.store.items()[0]["state"] == "READY"

    video.write_bytes(b"second version is different")

    def unexpected_upload(*_args, **_kwargs):
        raise AssertionError("changed source must be blocked before calling the uploader")

    monkeypatch.setattr(folder, "upload_video", unexpected_upload)
    result = publisher.publish_one()
    assert result is not None
    assert result["state"] == "SOURCE_CHANGED"
    assert "changed after review" in str(result["error"])
    assert publisher.store.items()[0]["state"] == "SOURCE_CHANGED"


def test_editing_source_during_upload_does_not_revoke_active_lease(
    tmp_path: Path, monkeypatch
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    video = incoming / "editing.mp4"
    video.write_bytes(b"initial version")
    monkeypatch.setattr(folder, "probe_video", _probe)
    publisher = _publisher(tmp_path)
    folder.approve_sidecar(incoming, video.name, ai_generated=False, realistic_alteration=False)
    publisher.scan()
    claimed = publisher.store.claim_next(max_per_day=2, now=1_800_000_000)
    assert claimed is not None
    old_id = int(claimed["id"])
    owner = str(claimed["lease_owner"])

    video.write_bytes(b"changed while the old snapshot is uploading")
    publisher.scan()

    old_item = publisher.store.get_item(old_id)
    assert old_item is not None
    assert old_item["state"] == "UPLOADING"
    assert old_item["lease_owner"] == owner
    assert any(
        item["state"] in {"WAITING_STABLE", "READY"}
        for item in publisher.store.items()
        if int(item["id"]) != old_id
    )
