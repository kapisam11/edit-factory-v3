from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ai_video_factory.folder_publisher import FolderPublisher, FolderPublisherConfig


def _make_publisher(
    tmp_path: Path,
    metadata: dict,
    *,
    account_id: str = "primary",
    allow_public: bool = False,
    max_videos_per_day: int = 1,
) -> tuple[FolderPublisher, Path, Path]:
    inbox = tmp_path / "inbox"
    state = tmp_path / "state"
    inbox.mkdir(parents=True)
    video = inbox / "story.mp4"
    video.write_bytes(b"stable test video bytes")
    sidecar = video.with_suffix(".json")
    sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    config = FolderPublisherConfig(
        inbox_dir=inbox,
        state_dir=state,
        queue_db=state / "folder-publishing.sqlite3",
        stability_seconds=0,
        max_input_bytes=1024 * 1024,
        max_duration_seconds=120,
        max_width=1920,
        max_height=1080,
        max_fps=60,
        max_videos_per_day=max_videos_per_day,
        account_id=account_id,
        allow_public=allow_public,
    )
    publisher = FolderPublisher(config)
    publisher._probe_video = lambda _path: {  # type: ignore[method-assign]
        "duration_seconds": 30.0,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
    }
    return publisher, video, sidecar


def _metadata(**overrides):
    data = {
        "title": "A verified test video",
        "description": "Test upload description",
        "tags": ["test", "short"],
        "platforms": ["youtube"],
        "privacy_status": "private",
        "approved": True,
        "rights_cleared": True,
    }
    data.update(overrides)
    return data


def _jobs(publisher: FolderPublisher):
    with publisher._connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM publishing_jobs ORDER BY platform")]


def test_scan_creates_one_durable_job_per_platform_and_fails_closed_for_missing_uploaders(tmp_path):
    publisher, _, _ = _make_publisher(
        tmp_path, _metadata(platforms=["youtube_shorts", "instagram_reels", "tiktok"])
    )

    first = publisher.scan()
    second = publisher.scan()
    jobs = _jobs(publisher)

    assert first["queued"] == 1
    assert second["queued"] == 0
    assert {job["platform"] for job in jobs} == {"youtube", "instagram", "tiktok"}
    assert {job["state"] for job in jobs if job["platform"] == "youtube"} == {"PENDING"}
    assert all(
        job["state"] == "BLOCKED_UNSUPPORTED"
        for job in jobs if job["platform"] in {"instagram", "tiktok"}
    )
    assert len({job["id"] for job in jobs}) == 3


def test_scan_requires_approval_and_rights_clearance(tmp_path):
    publisher, _, _ = _make_publisher(tmp_path, _metadata(approved=False, rights_cleared=False))

    result = publisher.scan()
    jobs = _jobs(publisher)

    assert result["queued"] == 0
    assert jobs[0]["state"] == "NEEDS_REVIEW"
    assert "approval is missing" in jobs[0]["last_error"]
    assert "rights clearance is missing" in jobs[0]["last_error"]


def test_metadata_change_for_same_video_never_silently_republishes(tmp_path):
    publisher, _, sidecar = _make_publisher(tmp_path, _metadata())
    publisher.scan()
    changed = _metadata(title="Changed title after queueing")
    sidecar.write_text(json.dumps(changed), encoding="utf-8")

    result = publisher.scan()
    jobs = _jobs(publisher)

    assert result["queued"] == 0
    assert len(jobs) == 1
    assert jobs[0]["state"] == "NEEDS_REVIEW"
    assert "metadata changed" in jobs[0]["last_error"]


def test_approve_job_requires_current_sidecar_approval_and_unchanged_source(tmp_path):
    publisher, _, sidecar = _make_publisher(
        tmp_path, _metadata(approved=False, rights_cleared=False)
    )
    publisher.scan()
    job_id = _jobs(publisher)[0]["id"]
    with pytest.raises(ValueError, match="approved=true"):
        publisher.approve_job(job_id)

    sidecar.write_text(json.dumps(_metadata()), encoding="utf-8")
    result = publisher.approve_job(job_id)

    assert result["status"] == "approved_for_queue"
    assert _jobs(publisher)[0]["state"] == "PENDING"


def test_approve_job_refuses_a_changed_source_content(tmp_path):
    publisher, video, sidecar = _make_publisher(
        tmp_path, _metadata(approved=False, rights_cleared=False)
    )
    publisher.scan()
    job_id = _jobs(publisher)[0]["id"]
    video.write_bytes(b"different video content")
    sidecar.write_text(json.dumps(_metadata()), encoding="utf-8")

    with pytest.raises(ValueError, match="source content changed"):
        publisher.approve_job(job_id)


def test_publish_persists_remote_video_id_and_is_not_repeated(tmp_path, monkeypatch):
    publisher, _, _ = _make_publisher(tmp_path, _metadata())
    publisher.scan()
    calls = []

    def fake_upload(video_path, **kwargs):
        calls.append({"video_path": video_path, **kwargs})
        return {"video_id": "remote-video-123", "url": "https://youtube.com/watch?v=remote-video-123"}

    monkeypatch.setattr("ai_video_factory.youtube_publisher.upload_video", fake_upload)
    result = publisher.publish_one()

    assert result["status"] == "published"
    jobs = _jobs(publisher)
    assert jobs[0]["state"] == "PUBLISHED"
    assert jobs[0]["remote_id"] == "remote-video-123"
    assert jobs[0]["remote_url"] == "https://youtube.com/watch?v=remote-video-123"
    assert len(calls) == 1
    assert calls[0]["idempotency_key"].startswith("folder-primary-")
    assert publisher.publish_one()["status"] == "no_eligible_jobs"
    assert len(calls) == 1


def test_interrupted_upload_reuses_the_same_idempotency_key(tmp_path, monkeypatch):
    publisher, _, _ = _make_publisher(tmp_path, _metadata())
    publisher.scan()
    job = _jobs(publisher)[0]
    with publisher._connect() as conn:
        conn.execute("UPDATE publishing_jobs SET state='UPLOADING',attempts=1 WHERE id=?", (job["id"],))
    keys = []

    def fake_upload(_video_path, **kwargs):
        keys.append(kwargs["idempotency_key"])
        return {"video_id": "recovered-remote-id", "url": "https://youtube.com/watch?v=recovered-remote-id"}

    monkeypatch.setattr("ai_video_factory.youtube_publisher.upload_video", fake_upload)
    result = publisher.publish_one()

    assert result["status"] == "published"
    assert keys == [f"folder-primary-{job['id']}"]


def test_publication_defaults_to_private_and_public_upload_is_gated(tmp_path):
    publisher, _, _ = _make_publisher(
        tmp_path, _metadata(privacy_status="public"), allow_public=False
    )
    publisher.scan()
    job = _jobs(publisher)[0]

    assert job["state"] == "NEEDS_REVIEW"
    assert "AIVF_FOLDER_ALLOW_PUBLIC=1" in job["last_error"]


def test_public_daily_limit_stops_second_upload(tmp_path):
    publisher, _, _ = _make_publisher(
        tmp_path, _metadata(), max_videos_per_day=1
    )
    publisher.scan()
    job = _jobs(publisher)[0]
    today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    with publisher._connect() as conn:
        conn.execute(
            "UPDATE publishing_jobs SET state='PUBLISHED',published_at=? WHERE id=?",
            (today, job["id"]),
        )

    result = publisher.publish_one(now=time.time())

    assert result["status"] == "daily_limit_reached"
    assert result["published_today"] == 1


def test_scan_rejects_invalid_or_oversized_media_before_queueing(tmp_path):
    publisher, video, _ = _make_publisher(tmp_path, _metadata())
    object.__setattr__(publisher.config, "max_input_bytes", 2)
    result = publisher.scan()

    assert result["queued"] == 0
    assert result["errors"]
    assert "input limit" in result["errors"][0]["error"]
