from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ai_video_factory.existing_file_publisher as publisher
from ai_video_factory.existing_file_publisher import (
    ExistingFilePublisher,
    NeedsReconciliation,
    PermanentPublishError,
    PublishStore,
    PublisherConfig,
    _safe_asset,
    _validate_metadata,
)


def _config(tmp_path: Path, **overrides) -> PublisherConfig:
    values = {
        "watch_dir": tmp_path / "watch",
        "state_db": tmp_path / "state" / "publishing.sqlite",
        "poll_seconds": 10,
        "stable_seconds": 10,
        "max_file_bytes": 10_000_000,
        "max_duration_seconds": 900,
        "max_videos_per_account_per_day": 1,
        "publish_time_utc": "00:00",
        "max_attempts": 2,
        "retry_base_seconds": 10,
        "allow_public": False,
        "platforms": ("youtube",),
    }
    values.update(overrides)
    return PublisherConfig(**values)


def _metadata(**overrides):
    values = {
        "title": "A reviewed video",
        "description": "A description",
        "platforms": ["youtube"],
        "rights_cleared": True,
        "youtube_privacy_status": "private",
    }
    values.update(overrides)
    return values


def test_sidecar_requires_explicit_rights_clearance(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(PermanentPublishError, match="rights_cleared"):
        _validate_metadata(_metadata(rights_cleared=False), config=config, watch_dir=config.watch_dir)


def test_public_post_requires_two_explicit_safety_switches(tmp_path):
    config = _config(tmp_path, allow_public=False)
    with pytest.raises(PermanentPublishError, match="Public publishing"):
        _validate_metadata(_metadata(youtube_privacy_status="public", public_publish_approved=True),
                           config=config, watch_dir=config.watch_dir)
    config = _config(tmp_path, allow_public=True)
    with pytest.raises(PermanentPublishError, match="Public publishing"):
        _validate_metadata(_metadata(youtube_privacy_status="public", public_publish_approved=False),
                           config=config, watch_dir=config.watch_dir)
    allowed = _validate_metadata(_metadata(youtube_privacy_status="public", public_publish_approved=True),
                                 config=config, watch_dir=config.watch_dir)
    assert allowed["youtube_privacy_status"] == "public"


def test_public_instagram_reels_needs_opt_in_and_per_file_approval(tmp_path):
    with pytest.raises(PermanentPublishError, match="Public publishing"):
        _validate_metadata(_metadata(platforms=["instagram"]),
                           config=_config(tmp_path, platforms=("instagram",)), watch_dir=tmp_path / "watch")
    config = _config(tmp_path, platforms=("instagram",), allow_public=True)
    allowed = _validate_metadata(_metadata(platforms=["instagram"], public_publish_approved=True),
                                 config=config, watch_dir=tmp_path / "watch")
    assert allowed["platforms"] == ["instagram"]


def test_sidecar_asset_must_stay_inside_watch_folder(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("not an upload asset", encoding="utf-8")
    with pytest.raises(PermanentPublishError, match="inside the watched folder"):
        _safe_asset(str(outside), watch_dir=watch, label="thumbnail_path")


def test_candidate_requires_stable_video_and_sidecar(tmp_path, monkeypatch):
    watch = tmp_path / "watch"
    watch.mkdir()
    video = watch / "one.mp4"
    sidecar = watch / "one.publish.json"
    video.write_bytes(b"video")
    sidecar.write_text(json.dumps(_metadata()), encoding="utf-8")
    clock = [100.0]
    monkeypatch.setattr(publisher.time, "time", lambda: clock[0])
    store = PublishStore(tmp_path / "state" / "queue.sqlite")
    assert store.candidate_is_stable(video, sidecar, stable_seconds=10) is False
    clock[0] += 11
    assert store.candidate_is_stable(video, sidecar, stable_seconds=10) is True
    video.write_bytes(b"video changed")
    assert store.candidate_is_stable(video, sidecar, stable_seconds=10) is False


def test_store_uses_lease_fencing_and_records_publish_state(tmp_path):
    config = _config(tmp_path)
    source = config.watch_dir / "one.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    store = PublishStore(config.state_db)
    job_id = store.enqueue(source=source, digest="a" * 64, platform="youtube",
                           account_key="channel-1", metadata=_metadata())
    claimed = store.claim_next(now_epoch=1000, now_utc=datetime(2026, 10, 11, 13, tzinfo=timezone.utc),
                               config=config, processed=set())
    assert claimed is not None and claimed["job_id"] == job_id
    with pytest.raises(RuntimeError, match="lease lost"):
        store.checkpoint(job_id, "wrong-token", {"stage": "uploading"})
    store.checkpoint(job_id, claimed["lease_token"], {"stage": "youtube_upload_in_flight"})
    with pytest.raises(RuntimeError, match="lease lost"):
        store.finish({**claimed, "lease_token": "wrong-token"}, state="PUBLISHED",
                     remote_state={"stage": "published"}, remote_id="video-1")
    store.finish(claimed, state="PUBLISHED", remote_state={"stage": "published"},
                 remote_id="video-1", remote_url="https://youtube.com/watch?v=video-1")
    row = store.status()[0]
    assert row["state"] == "PUBLISHED"
    assert row["remote_id"] == "video-1"


def test_existing_file_worker_queues_stable_sidecar_and_publishes_once(tmp_path, monkeypatch):
    config = _config(tmp_path, stable_seconds=10)
    config.watch_dir.mkdir(parents=True)
    video = config.watch_dir / "clip.mp4"
    video.write_bytes(b"not a real video; ffprobe is mocked")
    video.with_suffix(".publish.json").write_text(json.dumps(_metadata()), encoding="utf-8")
    monkeypatch.setattr(publisher, "_account_ids", lambda: {
        "youtube": "channel-123", "instagram": "unconfigured-instagram", "tiktok": "unconfigured-tiktok"
    })
    monkeypatch.setattr(PublishStore, "candidate_is_stable", lambda *args, **kwargs: True)
    monkeypatch.setattr(publisher, "_probe_media", lambda *args, **kwargs: {
        "duration_seconds": 30.0, "width": 1080, "height": 1920
    })
    monkeypatch.setattr(publisher, "_utc_now", lambda: datetime(2026, 10, 11, 13, tzinfo=timezone.utc))
    uploads = []

    def fake_upload(*args, **kwargs):
        uploads.append((args, kwargs))
        return {"video_id": "remote-123", "url": "https://www.youtube.com/watch?v=remote-123"}

    monkeypatch.setattr("ai_video_factory.youtube_publisher.upload_video", fake_upload)
    worker = ExistingFilePublisher(config)
    worker.account_ids["youtube"] = "channel-123"
    assert worker.run_once() == 1
    assert worker.run_once() == 0
    rows = worker.store.status()
    assert len(rows) == 1
    assert rows[0]["state"] == "PUBLISHED"
    assert rows[0]["remote_id"] == "remote-123"
    assert len(uploads) == 1
    assert uploads[0][1]["idempotency_key"] == f"folder-{rows[0]['job_id']}"


def test_uncertain_youtube_outcome_becomes_reconciliation_not_retry(tmp_path, monkeypatch):
    config = _config(tmp_path, stable_seconds=10)
    config.watch_dir.mkdir(parents=True)
    video = config.watch_dir / "clip.mp4"
    video.write_bytes(b"video")
    video.with_suffix(".publish.json").write_text(json.dumps(_metadata()), encoding="utf-8")
    monkeypatch.setattr(publisher, "_account_ids", lambda: {
        "youtube": "channel-123", "instagram": "unconfigured-instagram", "tiktok": "unconfigured-tiktok"
    })
    monkeypatch.setattr(PublishStore, "candidate_is_stable", lambda *args, **kwargs: True)
    monkeypatch.setattr(publisher, "_probe_media", lambda *args, **kwargs: {
        "duration_seconds": 30.0, "width": 1080, "height": 1920
    })
    monkeypatch.setattr(publisher, "_utc_now", lambda: datetime(2026, 10, 11, 13, tzinfo=timezone.utc))

    def ambiguous_upload(*args, **kwargs):
        raise RuntimeError("The previous YouTube upload outcome is uncertain; refusing duplicate")

    monkeypatch.setattr("ai_video_factory.youtube_publisher.upload_video", ambiguous_upload)
    worker = ExistingFilePublisher(config)
    worker.account_ids["youtube"] = "channel-123"
    worker.run_once()
    row = worker.store.status()[0]
    assert row["state"] == "NEEDS_RECONCILIATION"
    assert "reconciliation" in row["last_error"].lower()
    saved_state = json.loads(worker.store.connect().execute(
        "SELECT remote_state_json FROM file_publish_jobs WHERE job_id=?", (row["job_id"],)
    ).fetchone()[0])
    assert saved_state["stage"] == "youtube_upload_in_flight"


def test_instagram_publishing_stage_never_retries_without_reconciliation(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "ig-user")
    with pytest.raises(NeedsReconciliation, match="verify the account"):
        publisher._publish_instagram(Path("clip.mp4"), _metadata(platforms=["instagram"]),
                                     {"stage": "ig_publishing", "container_id": "container-1"},
                                     lambda _state: pytest.fail("must not checkpoint or call publish again"), "job-1")


def test_tiktok_interrupted_upload_never_retries_chunk_blindly(monkeypatch, tmp_path):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("TIKTOK_OPEN_ID", "open-id")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    with pytest.raises(NeedsReconciliation, match="transfer was interrupted"):
        publisher._publish_tiktok(video, _metadata(platforms=["tiktok"]),
                                  {"stage": "tiktok_upload_in_flight", "publish_id": "p1",
                                   "upload_url": "https://open-upload.tiktokapis.com/upload"},
                                  lambda _state: pytest.fail("must not reupload an ambiguous chunk"), "job-1")


def test_requested_platforms_can_be_validated_independently(tmp_path):
    config = _config(tmp_path, platforms=("youtube",))
    selected = _validate_metadata(_metadata(platforms=["tiktok"]), config=config, watch_dir=config.watch_dir)
    assert selected["platforms"] == ["tiktok"]
