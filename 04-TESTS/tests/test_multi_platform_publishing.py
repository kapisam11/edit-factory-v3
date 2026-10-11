from __future__ import annotations

import json
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
        PlatformPublishError("temporary upstream timeout", code="NETWORK", retryable=True, uncertain=True),
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
