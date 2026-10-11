from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ai_video_factory import folder_publisher as folder
from ai_video_factory.multi_platform_publishing import (
    MultiPlatformPublisher,
    PublicationStore,
    Target,
    publication_key,
)
from ai_video_factory.platform_adapters import PlatformPublishError, PublishResult


def _item(tmp_path: Path, item_id: int = 1) -> dict[str, object]:
    source = tmp_path / f"clip-{item_id}.mp4"
    source.write_bytes(b"same fixture bytes")
    return {
        "id": item_id,
        "path": str(source),
        "size_bytes": source.stat().st_size,
        "mtime_ns": source.stat().st_mtime_ns,
        "sidecar_mtime_ns": None,
    }


def _enqueue(store: PublicationStore, tmp_path: Path, publication_id: str, target: Target, now: float = 1_800_000_000.0) -> int:
    item = _item(tmp_path, len(store.jobs()) + 1)
    source = Path(str(item["path"]))
    staged = tmp_path / f"staged-{publication_id}-{target.platform}-{target.account_id}.mp4"
    staged.write_bytes(source.read_bytes())
    job_id, created = store.enqueue(
        publication_id=publication_id,
        item=item,
        source_sha256="a" * 64,
        staged_path=staged,
        target=target,
        metadata={"title": "Fixture", "privacy_status": "private"},
        max_attempts=4,
        now=now,
    )
    assert created
    return job_id


def test_target_parser_is_strict_and_account_scoped() -> None:
    assert Target.parse("youtube:default") == Target("youtube", "default")
    assert Target.parse("instagram:brand_main") == Target("instagram", "brand_main")
    assert Target.parse("tiktok:creator-1") == Target("tiktok", "creator-1")
    with pytest.raises(ValueError):
        Target.parse("youtube")
    with pytest.raises(ValueError):
        Target.parse("other:default")
    with pytest.raises(ValueError):
        Target.parse("youtube:account with spaces")


def test_publication_identity_is_not_only_the_file_hash(tmp_path: Path) -> None:
    one = tmp_path / "one.mp4"
    two = tmp_path / "two.mp4"
    one.write_bytes(b"same bytes")
    two.write_bytes(b"same bytes")
    identity_one = publication_key({}, source_path=str(one), source_sha256="a" * 64)
    identity_two = publication_key({}, source_path=str(two), source_sha256="a" * 64)
    assert identity_one != identity_two
    assert publication_key(
        {"publication_id": "repost-2"},
        source_path=str(one),
        source_sha256="a" * 64,
    ) == "repost-2"


def test_store_keeps_platform_account_jobs_independent_and_idempotent(tmp_path: Path) -> None:
    store = PublicationStore(tmp_path / "state" / "publish.sqlite")
    first = _enqueue(store, tmp_path, "video-1", Target("youtube", "default"))
    second = _enqueue(store, tmp_path, "video-1", Target("instagram", "business"))
    third = _enqueue(store, tmp_path, "video-1", Target("tiktok", "creator"))
    assert len({first, second, third}) == 3
    repeated, created = store.enqueue(
        publication_id="video-1",
        item=_item(tmp_path, 50),
        source_sha256="a" * 64,
        staged_path=tmp_path / "staged-video-1-youtube-default.mp4",
        target=Target("youtube", "default"),
        metadata={"title": "changed but existing job not silently mutated"},
        max_attempts=4,
        now=1_800_000_001.0,
    )
    assert repeated == first
    assert created is False
    assert store.counts() == {"queued": 3}


def test_store_rejects_reusing_publication_id_for_different_media(tmp_path: Path) -> None:
    store = PublicationStore(tmp_path / "publish.sqlite")
    target = Target("youtube", "default")
    _enqueue(store, tmp_path, "same-id", target)
    item = _item(tmp_path, 20)
    staged = tmp_path / "different.mp4"
    staged.write_bytes(b"different bytes")
    with pytest.raises(ValueError, match="different media"):
        store.enqueue(
            publication_id="same-id",
            item=item,
            source_sha256="b" * 64,
            staged_path=staged,
            target=target,
            metadata={},
            max_attempts=4,
            now=1_800_000_001.0,
        )


def test_claim_fences_same_account_but_allows_other_destinations(tmp_path: Path) -> None:
    store = PublicationStore(tmp_path / "publish.sqlite")
    _enqueue(store, tmp_path, "one", Target("youtube", "default"))
    _enqueue(store, tmp_path, "two", Target("youtube", "default"))
    _enqueue(store, tmp_path, "three", Target("instagram", "business"))
    now = 1_800_000_000.0
    first = store.claim_next(max_per_day=10, now=now)
    second = store.claim_next(max_per_day=10, now=now)
    assert first is not None and second is not None
    assert (first["platform"], first["account_id"]) != (second["platform"], second["account_id"])
    assert store.claim_next(max_per_day=10, now=now) is None


def test_configured_daily_cap_allows_multiple_posts_when_limit_is_greater_than_one(
    tmp_path: Path,
) -> None:
    store = PublicationStore(tmp_path / "publish.sqlite")
    target = Target("youtube", "default")
    first_id = _enqueue(store, tmp_path, "first", target)
    second_id = _enqueue(store, tmp_path, "second", target)
    now = 1_800_000_000.0
    first = store.claim_next(max_per_day=2, now=now)
    assert first is not None and first["id"] == first_id
    store.mark_published(
        first_id,
        str(first["lease_owner"]),
        PublishResult("youtube", "remote-1", "remote-1", "https://youtube.test/remote-1"),
        now=now + 1,
    )
    second = store.claim_next(max_per_day=2, now=now + 2)
    assert second is not None and second["id"] == second_id


def test_failed_notifications_can_retry_but_successful_notifications_are_deduplicated(
    tmp_path: Path,
) -> None:
    store = PublicationStore(tmp_path / "publish.sqlite")
    key = "incident:one"
    assert store.claim_notification(key, now=1000.0) is True
    assert store.claim_notification(key, now=1001.0) is False
    store.set_notification_response(key, 503)
    assert store.claim_notification(key, now=1100.0) is False
    assert store.claim_notification(key, now=1301.0) is True
    store.set_notification_response(key, 204)
    assert store.claim_notification(key, now=99999.0) is False


def test_retryable_destination_failure_does_not_mark_sibling_destinations_failed(tmp_path: Path) -> None:
    store = PublicationStore(tmp_path / "publish.sqlite")
    first_id = _enqueue(store, tmp_path, "video-1", Target("youtube", "default"))
    second_id = _enqueue(store, tmp_path, "video-1", Target("tiktok", "creator"))
    now = 1_800_000_000.0
    first = store.claim_next(max_per_day=10, now=now)
    assert first is not None
    state = store.mark_failure(
        first,
        str(first["lease_owner"]),
        PlatformPublishError("temporary upstream timeout before a remote request was sent", code="NETWORK", retryable=True, uncertain=False),
        now=now + 1,
    )
    assert state == "retryable_failed"
    rows = {row["id"]: row for row in store.jobs()}
    assert rows[first_id]["state"] == "retryable_failed"
    assert rows[second_id]["state"] == "queued"
    history = store.history(first_id)
    assert [event["event_type"] for event in history][:2] == ["failure_recorded", "attempt_started"]


def test_folder_ingestion_can_seed_three_independent_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    video = incoming / "multi.mp4"
    video.write_bytes(b"stable fake media")
    monkeypatch.setattr(
        folder,
        "probe_video",
        lambda _path, *, max_duration_seconds: {
            "duration_seconds": 12.0,
            "width": 720,
            "height": 1280,
            "video_codec": "h264",
            "format_name": "mp4",
            "audio_streams": 1,
        },
    )
    sidecar = folder.approve_sidecar(
        incoming,
        video.name,
        ai_generated=False,
        realistic_alteration=False,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["publication_id"] = "multi-platform-fixture"
    payload["destinations"] = ["youtube:default", "instagram:brand", "tiktok:creator"]
    payload["platform_overrides"] = {
        "instagram:brand": {"video_url": "https://media.example.test/multi.mp4"},
        "tiktok:creator": {"privacy_level": "SELF_ONLY"},
    }
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    base = folder.FolderPublisher(
        folder.FolderPublisherConfig(
            watch_dir=incoming,
            db_path=tmp_path / "state" / "publisher.sqlite",
            settle_seconds=0,
            poll_seconds=1,
            publish_enabled=False,
        )
    )
    multi = MultiPlatformPublisher(base, publish_enabled=False)
    result = multi.scan_and_enqueue()
    assert result["publication_jobs_added"] == 3
    rows = multi.store.jobs()
    assert {(row["platform"], row["account_id"], row["state"]) for row in rows} == {
        ("youtube", "default", "queued"),
        ("instagram", "brand", "queued"),
        ("tiktok", "creator", "queued"),
    }
    with multi.store._connect() as connection:
        keys = [row[0] for row in connection.execute("SELECT idempotency_key FROM platform_publications")]
    assert len(set(keys)) == 3


def test_folder_publisher_allows_explicit_repost_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    video = incoming / "repost.mp4"
    video.write_bytes(b"same media, intentional repost")
    monkeypatch.setattr(
        folder,
        "probe_video",
        lambda _path, *, max_duration_seconds: {
            "duration_seconds": 10.0,
            "width": 720,
            "height": 1280,
            "video_codec": "h264",
            "format_name": "mp4",
            "audio_streams": 1,
        },
    )
    sidecar = folder.approve_sidecar(
        incoming,
        video.name,
        ai_generated=False,
        realistic_alteration=False,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["publication_id"] = "repost-one"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    publisher = folder.FolderPublisher(
        folder.FolderPublisherConfig(
            watch_dir=incoming,
            db_path=tmp_path / "state" / "publisher.sqlite",
            settle_seconds=0,
            poll_seconds=1,
            max_videos_per_day=5,
            publish_enabled=True,
        )
    )
    calls: list[str] = []

    def fake_upload(_path: str, **kwargs: object) -> dict[str, object]:
        calls.append(str(kwargs["idempotency_key"]))
        return {"video_id": f"remote-{len(calls)}", "url": f"https://youtube.test/{len(calls)}"}

    monkeypatch.setattr(folder, "upload_video", fake_upload)
    publisher.scan()
    first = publisher.publish_one()
    assert first is not None and first["state"] == "PUBLISHED"
    payload["publication_id"] = "repost-two"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    publisher.scan()
    second = publisher.publish_one()
    assert second is not None and second["state"] == "PUBLISHED"
    assert len(calls) == 2
    assert calls[0] != calls[1]
    assert calls[0].startswith("folder-v2-")

def test_shared_staging_survives_source_change_until_all_destinations_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    video = incoming / "source.mp4"
    video.write_bytes(b"original immutable input")

    monkeypatch.setattr(
        folder,
        "probe_video",
        lambda _path, *, max_duration_seconds: {
            "duration_seconds": 12.0,
            "width": 720,
            "height": 1280,
            "video_codec": "h264",
            "format_name": "mp4",
            "audio_streams": 1,
        },
    )
    sidecar = folder.approve_sidecar(
        incoming,
        video.name,
        ai_generated=False,
        realistic_alteration=False,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["publication_id"] = "shared-staging-001"
    payload["destinations"] = ["youtube:default", "tiktok:creator"]
    payload["platform_overrides"] = {"tiktok:creator": {"privacy_level": "SELF_ONLY"}}
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    base = folder.FolderPublisher(
        folder.FolderPublisherConfig(
            watch_dir=incoming,
            db_path=tmp_path / "state" / "publisher.sqlite",
            settle_seconds=0,
            poll_seconds=1,
            publish_enabled=False,
        )
    )
    multi = MultiPlatformPublisher(base, publish_enabled=False)
    prepared = multi.scan_and_enqueue()
    assert prepared["publication_jobs_added"] == 2

    original_item = next(item for item in base.store.items() if item["path"] == str(video.resolve()))
    original_id = int(original_item["id"])
    original_stage = Path(str(original_item["staged_path"]))
    assert original_stage.is_file()

    # Once a source is routed to the per-destination queue, the legacy single
    # destination publisher must not independently claim the same source row.
    assert base.store.claim_next(max_per_day=10, now=1_800_000_000.0) is None

    video.write_bytes(b"edited input is a different publication")
    base.scan()
    base.scan()
    old_item = base.store.get_item(original_id)
    assert old_item is not None and old_item["state"] == "SOURCE_CHANGED"
    assert original_stage.is_file()

    # Even though the watch-folder row is terminal, the old immutable bytes are
    # still referenced by two destination jobs and cannot be collected yet.
    assert base.store.cleanup_terminal_staging(base.staging_dir) == 0
    assert original_stage.is_file()

    now = time.time() + 10
    for index in range(2):
        claimed = multi.store.claim_next(max_per_day=10, now=now + index * 2)
        assert claimed is not None
        destination = f"{claimed['platform']}-{claimed['account_id']}"
        remote_id = f"remote-{destination}"
        multi.store.mark_published(
            int(claimed["id"]),
            str(claimed["lease_owner"]),
            PublishResult(
                str(claimed["platform"]),
                remote_id,
                remote_id,
                f"https://example.test/{remote_id}",
            ),
            now=now + index * 2 + 1,
        )

    # Once every destination has confirmed publication, cleanup can safely
    # remove the snapshot even though the local source has since changed.
    base.store.cleanup_terminal_staging(base.staging_dir)
    assert not original_stage.exists()
    assert base.store.get_item(original_id)["staged_path"] is None

def test_folder_row_and_staging_finalize_only_after_every_destination_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    video = incoming / "complete.mp4"
    video.write_bytes(b"completed destinations fixture")

    monkeypatch.setattr(
        folder,
        "probe_video",
        lambda _path, *, max_duration_seconds: {
            "duration_seconds": 8.0,
            "width": 720,
            "height": 1280,
            "video_codec": "h264",
            "format_name": "mp4",
            "audio_streams": 1,
        },
    )
    sidecar = folder.approve_sidecar(
        incoming,
        video.name,
        ai_generated=False,
        realistic_alteration=False,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["publication_id"] = "finalize-all-destinations"
    payload["destinations"] = ["youtube:default", "tiktok:creator"]
    payload["platform_overrides"] = {"tiktok:creator": {"privacy_level": "SELF_ONLY"}}
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    base = folder.FolderPublisher(
        folder.FolderPublisherConfig(
            watch_dir=incoming,
            db_path=tmp_path / "state" / "publisher.sqlite",
            settle_seconds=0,
            poll_seconds=1,
            publish_enabled=False,
        )
    )
    multi = MultiPlatformPublisher(base, publish_enabled=False)
    assert multi.scan_and_enqueue()["publication_jobs_added"] == 2
    item = next(row for row in base.store.items() if row["path"] == str(video.resolve()))
    source_id = int(item["id"])
    stage = Path(str(item["staged_path"]))
    now = time.time() + 10

    first = multi.store.claim_next(max_per_day=10, now=now)
    assert first is not None
    multi.store.mark_published(
        int(first["id"]),
        str(first["lease_owner"]),
        PublishResult(str(first["platform"]), "remote-first", "remote-first", "https://example.test/first"),
        now=now + 1,
    )
    assert multi._finalize_source_if_complete(source_id, now=now + 1) is False
    assert base.store.get_item(source_id)["state"] == "READY"
    assert stage.is_file()

    second = multi.store.claim_next(max_per_day=10, now=now + 2)
    assert second is not None
    multi.store.mark_published(
        int(second["id"]),
        str(second["lease_owner"]),
        PublishResult(str(second["platform"]), "remote-second", "remote-second", "https://example.test/second"),
        now=now + 3,
    )
    assert multi._finalize_source_if_complete(source_id, now=now + 3) is True
    assert base.store.get_item(source_id)["state"] == "PUBLISHED"
    base.store.cleanup_terminal_staging(base.staging_dir)
    assert not stage.exists()
    assert base.store.get_item(source_id)["staged_path"] is None

def test_uncertain_create_without_remote_operation_id_fails_closed(
    tmp_path: Path,
) -> None:
    store = PublicationStore(tmp_path / "publish.sqlite")
    youtube_id = _enqueue(store, tmp_path, "ambiguous-one", Target("youtube", "default"))
    sibling_id = _enqueue(store, tmp_path, "ambiguous-two", Target("youtube", "default"))
    now = 1_800_000_000.0
    job = store.claim_next(max_per_day=10, now=now)
    assert job is not None and job["id"] == youtube_id

    state = store.mark_failure(
        job,
        str(job["lease_owner"]),
        PlatformPublishError(
            "connection dropped after create request",
            code="AMBIGUOUS_CREATE",
            retryable=True,
            uncertain=True,
        ),
        now=now + 1,
    )
    assert state == "permanent_failed"
    rows = {row["id"]: row for row in store.jobs()}
    assert rows[youtube_id]["error_code"] == "AMBIGUOUS_OUTCOME_UNTRACKED"
    assert rows[sibling_id]["state"] == "queued"
    assert "manually" in rows[youtube_id]["last_error"]

def test_manual_reconciliation_records_confirmed_remote_publication(tmp_path: Path) -> None:
    store = PublicationStore(tmp_path / "publish.sqlite")
    job_id = _enqueue(store, tmp_path, "reconcile-published", Target("tiktok", "creator"))
    now = 1_800_000_000.0
    job = store.claim_next(max_per_day=10, now=now)
    assert job is not None and job["id"] == job_id
    assert store.mark_failure(
        job,
        str(job["lease_owner"]),
        PlatformPublishError(
            "creation response was lost",
            code="AMBIGUOUS_CREATE",
            retryable=True,
            uncertain=True,
        ),
        now=now + 1,
    ) == "permanent_failed"

    state = store.reconcile_failed(
        job_id,
        outcome="published",
        remote_post_id="confirmed-remote-post",
        remote_url="https://www.tiktok.com/@creator/video/confirmed-remote-post",
        now=now + 2,
    )
    assert state == "published"
    row = next(job for job in store.jobs() if job["id"] == job_id)
    assert row["remote_post_id"] == "confirmed-remote-post"
    assert row["remote_url"] == "https://www.tiktok.com/@creator/video/confirmed-remote-post"
    assert row["error_code"] is None
    assert store.history(job_id)[0]["event_type"] == "manual_reconciliation"


def test_manual_reconciliation_of_instagram_absence_reuses_container_without_reposting(
    tmp_path: Path,
) -> None:
    store = PublicationStore(tmp_path / "publish.sqlite")
    job_id = _enqueue(store, tmp_path, "reconcile-instagram", Target("instagram", "brand"))
    now = 1_800_000_000.0
    job = store.claim_next(max_per_day=10, now=now)
    assert job is not None and job["id"] == job_id
    store.record_operation(
        job_id,
        str(job["lease_owner"]),
        "instagram-container-123",
        {
            "container_id": "instagram-container-123",
            "stage": "publishing",
            "publish_started": True,
            "publish_started_at": now,
        },
        "processing_remote",
        now=now + 1,
    )
    assert store.mark_failure(
        job,
        str(job["lease_owner"]),
        PlatformPublishError(
            "publish result not confirmed",
            code="AMBIGUOUS_OUTCOME",
            retryable=False,
            uncertain=True,
        ),
        now=now + 2,
    ) == "permanent_failed"

    state = store.reconcile_failed(job_id, outcome="not-published", now=now + 3)
    assert state == "processing_remote"
    with store._connect() as connection:
        row = connection.execute(
            "SELECT remote_operation_id,adapter_state_json,state FROM platform_publications WHERE id=?",
            (job_id,),
        ).fetchone()
    adapter_state = json.loads(row["adapter_state_json"])
    assert row["remote_operation_id"] == "instagram-container-123"
    assert row["state"] == "processing_remote"
    assert adapter_state["container_id"] == "instagram-container-123"
    assert adapter_state["publish_started"] is False


def test_manual_reconciliation_rejects_unrelated_permanent_errors(tmp_path: Path) -> None:
    store = PublicationStore(tmp_path / "publish.sqlite")
    job_id = _enqueue(store, tmp_path, "not-ambiguous", Target("youtube", "default"))
    now = 1_800_000_000.0
    job = store.claim_next(max_per_day=10, now=now)
    assert job is not None and job["id"] == job_id
    assert store.mark_failure(
        job,
        str(job["lease_owner"]),
        PlatformPublishError("invalid privacy setting", code="INVALID_METADATA", retryable=False),
        now=now + 1,
    ) == "permanent_failed"
    with pytest.raises(ValueError, match="ambiguous remote publication"):
        store.reconcile_failed(
            job_id,
            outcome="published",
            remote_post_id="must-not-be-accepted",
            now=now + 2,
        )

def _deadline_publisher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MultiPlatformPublisher:
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    base = folder.FolderPublisher(
        folder.FolderPublisherConfig(
            watch_dir=incoming,
            db_path=tmp_path / "state" / "publisher.sqlite",
            settle_seconds=0,
            poll_seconds=1,
            publish_enabled=False,
        )
    )
    monkeypatch.setenv("AIVF_REQUIRE_DAILY_PUBLISH", "1")
    monkeypatch.setenv("AIVF_PUBLISH_DEADLINE_HOUR", "23")
    monkeypatch.setenv("AIVF_PUBLISH_DEADLINE_MINUTE", "0")
    monkeypatch.delenv("AIVF_FAILURE_WEBHOOK_URL", raising=False)
    return MultiPlatformPublisher(base, publish_enabled=False)


def test_required_daily_deadline_is_durable_and_alerted_once_per_local_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime

    publisher = _deadline_publisher(tmp_path, monkeypatch)
    now = datetime.now().astimezone().replace(hour=23, minute=59, second=0, microsecond=0)
    assert publisher.check_daily_deadline(now) is True
    assert publisher.check_daily_deadline(now) is True
    with publisher.store._connect() as connection:
        rows = connection.execute("SELECT local_date FROM platform_publication_deadline_alerts").fetchall()
    assert rows == [(now.date().isoformat(),)] or [row[0] for row in rows] == [now.date().isoformat()]


def test_required_daily_deadline_passes_only_when_all_destinations_are_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime

    publisher = _deadline_publisher(tmp_path, monkeypatch)
    now = datetime.now().astimezone().replace(hour=23, minute=59, second=0, microsecond=0)
    deadline_epoch = now.replace(hour=23, minute=0, second=0, microsecond=0).timestamp()
    created_at = deadline_epoch - 60
    yt = _enqueue(publisher.store, tmp_path, "daily-complete", Target("youtube", "default"), now=created_at)
    tt = _enqueue(publisher.store, tmp_path, "daily-complete", Target("tiktok", "creator"), now=created_at)
    for expected_id in (yt, tt):
        claimed = publisher.store.claim_next(max_per_day=10, now=deadline_epoch - 10)
        assert claimed is not None and claimed["id"] == expected_id
        remote = f"remote-{expected_id}"
        publisher.store.mark_published(
            expected_id,
            str(claimed["lease_owner"]),
            PublishResult(str(claimed["platform"]), remote, remote, f"https://example.test/{remote}"),
            now=deadline_epoch - 2,
        )
    assert publisher.check_daily_deadline(now) is False


def test_late_publication_does_not_clear_a_missed_daily_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime

    publisher = _deadline_publisher(tmp_path, monkeypatch)
    now = datetime.now().astimezone().replace(hour=23, minute=59, second=0, microsecond=0)
    deadline_epoch = now.replace(hour=23, minute=0, second=0, microsecond=0).timestamp()
    created_at = deadline_epoch - 60
    yt = _enqueue(publisher.store, tmp_path, "daily-late", Target("youtube", "default"), now=created_at)
    tt = _enqueue(publisher.store, tmp_path, "daily-late", Target("tiktok", "creator"), now=created_at)
    for expected_id in (yt, tt):
        claimed = publisher.store.claim_next(max_per_day=10, now=deadline_epoch + 1)
        assert claimed is not None and claimed["id"] == expected_id
        remote = f"late-remote-{expected_id}"
        publisher.store.mark_published(
            expected_id,
            str(claimed["lease_owner"]),
            PublishResult(str(claimed["platform"]), remote, remote, f"https://example.test/{remote}"),
            now=deadline_epoch + 10,
        )

    # The final destination did publish today, but after the configured cutoff.
    # The missed-deadline alert must therefore still fire.
    assert publisher.check_daily_deadline(now) is True
    with publisher.store._connect() as connection:
        rows = connection.execute(
            "SELECT local_date FROM platform_publication_deadline_alerts"
        ).fetchall()
    assert [row[0] for row in rows] == [now.date().isoformat()]

def test_instagram_can_use_configured_media_host_for_local_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_video_factory import media_host, multi_platform_publishing as multi_module

    incoming = tmp_path / "incoming"
    incoming.mkdir()
    video = incoming / "instagram.mp4"
    video.write_bytes(b"local video awaiting public media hosting")
    monkeypatch.setattr(
        folder,
        "probe_video",
        lambda _path, *, max_duration_seconds: {
            "duration_seconds": 8.0,
            "width": 720,
            "height": 1280,
            "video_codec": "h264",
            "format_name": "mp4",
            "audio_streams": 1,
        },
    )
    sidecar = folder.approve_sidecar(
        incoming,
        video.name,
        ai_generated=False,
        realistic_alteration=False,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["publication_id"] = "instagram-hosted-local-source"
    payload["destinations"] = ["instagram:brand"]
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    base = folder.FolderPublisher(
        folder.FolderPublisherConfig(
            watch_dir=incoming,
            db_path=tmp_path / "state" / "publisher.sqlite",
            settle_seconds=0,
            poll_seconds=1,
            publish_enabled=False,
        )
    )
    publisher = MultiPlatformPublisher(
        base,
        publish_enabled=True,
        max_per_account_per_day=2,
        min_free_disk_bytes=0,
    )

    class FakeHost:
        def upload_for_publication(self, media_path, **kwargs):
            assert media_path.is_file()
            assert kwargs["platform"] == "instagram"
            assert kwargs["account_id"] == "brand"
            assert kwargs["source_sha256"]
            return media_host.HostedMedia(
                object_key="edit-factory/instagram/brand/ab/object.mp4",
                url="https://media.example.test/signed/object.mp4?X-Amz-Signature=opaque",
                expires_at=time.time() + 3600,
            )

        def presign_get(self, object_key):
            return media_host.HostedMedia(
                object_key=object_key,
                url="https://media.example.test/signed/object.mp4?X-Amz-Signature=renewed",
                expires_at=time.time() + 3600,
            )

    monkeypatch.setattr(
        media_host.S3CompatibleMediaHost,
        "from_environment",
        classmethod(lambda _cls: FakeHost()),
    )
    seen: dict[str, object] = {}

    class FakeAdapter:
        def publish(self, **kwargs):
            metadata = kwargs["metadata"]
            state = kwargs["adapter_state"]
            assert metadata["_platform_override"]["video_url"].startswith("https://media.example.test/")
            assert state["media_host_object_key"].startswith("edit-factory/")
            seen["media_url"] = metadata["_platform_override"]["video_url"]
            seen["object_key"] = state["media_host_object_key"]
            next_state = {
                **state,
                "container_id": "instagram-container-hosted",
                "stage": "publishing",
                "publish_started": True,
            }
            kwargs["on_operation"](
                "instagram-container-hosted",
                next_state,
                "processing_remote",
            )
            return PublishResult(
                "instagram",
                "instagram-container-hosted",
                "instagram-post-hosted",
                "https://www.instagram.com/reel/instagram-post-hosted/",
            )

    monkeypatch.setattr(multi_module, "adapter_for", lambda _platform: FakeAdapter())
    prepared = publisher.scan_and_enqueue()
    assert prepared["publication_jobs_added"] == 1
    result = publisher.process_one()
    assert result is not None and result["state"] == "published"
    assert seen["object_key"] == "edit-factory/instagram/brand/ab/object.mp4"
    with publisher.store._connect() as connection:
        row = connection.execute(
            "SELECT state,adapter_state_json,remote_post_id FROM platform_publications"
        ).fetchone()
    saved_state = json.loads(row["adapter_state_json"])
    assert row["state"] == "published"
    assert row["remote_post_id"] == "instagram-post-hosted"
    assert saved_state["media_host_url"].startswith("https://media.example.test/")
    assert saved_state["container_id"] == "instagram-container-hosted"
