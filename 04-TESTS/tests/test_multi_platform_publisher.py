from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from ai_video_factory import publishing_queue as queue
from ai_video_factory import platform_publishers as adapters


def _media(path: Path) -> dict[str, object]:
    return {
        "duration_seconds": 12.0,
        "size_bytes": path.stat().st_size,
        "width": 1080,
        "height": 1920,
        "fps": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "audio_streams": 1,
        "audio_channels": 2,
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "extension": path.suffix.lower(),
        "quality_warnings": [],
    }


def _config(tmp_path: Path, *, settle: int = 0, publish: bool = False) -> queue.PublisherConfig:
    incoming = tmp_path / "incoming"
    incoming.mkdir(exist_ok=True)
    state = tmp_path / "state"
    return queue.PublisherConfig(
        watch_dir=incoming,
        state_dir=state,
        db_path=state / "publisher.sqlite",
        staging_dir=state / "staging",
        settle_seconds=settle,
        poll_seconds=1,
        max_file_bytes=1_000_000,
        max_staged_bytes=2_000_000,
        max_duration_seconds=900,
        max_width=3840,
        max_height=2160,
        max_fps=120,
        max_audio_channels=8,
        min_free_disk_bytes=0,
        max_attempts=3,
        retry_base_seconds=10,
        retry_max_seconds=60,
        lease_seconds=60,
        batch_size=3,
        publish_enabled=publish,
        allow_public=False,
        decode_check=False,
        quality_scan=False,
        expected_posts_per_day=1,
    )


def _prepared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    destinations: list[dict[str, str]] | None = None,
    *,
    settle: int = 0,
    publish: bool = False,
    service_adapters: dict[str, queue.Adapter] | None = None,
) -> tuple[queue.PublisherService, Path, Path]:
    config = _config(tmp_path, settle=settle, publish=publish)
    video = config.watch_dir / "clip.mp4"
    video.write_bytes(b"stable sample media bytes")
    monkeypatch.setattr(queue, "probe_media", _media)
    sidecar = queue.approve_video(
        video,
        config.watch_dir,
        destinations=destinations or [{"platform": "youtube", "account_id": "primary"}],
        ai_generated=False,
        realistic_alteration=False,
    )
    if service_adapters is None:
        service_adapters = {"youtube": _FakeAdapter()}
    service = queue.PublisherService(config, adapters=service_adapters)
    return service, video, sidecar


class _FakeAdapter:
    safe_retry_without_remote_id = True

    def __init__(self) -> None:
        self.starts = 0
        self.polls = 0

    def start(self, job, checkpoint):
        self.starts += 1
        checkpoint("remote-123", {"phase": "processing"})
        return queue.AdapterUpdate(
            "processing_remote",
            "remote-123",
            None,
            {"phase": "processing"},
        )

    def poll(self, job, checkpoint):
        self.polls += 1
        return queue.AdapterUpdate(
            "published",
            str(job["remote_id"]),
            "https://example.test/published",
            {"phase": "published"},
        )


def test_approval_is_bound_to_exact_bytes_and_declared_destinations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, video, sidecar = _prepared(tmp_path, monkeypatch)
    first = service.scan()
    assert first["ready_jobs_added"] == 1
    item = json.loads(sidecar.read_text(encoding="utf-8"))
    assert item["approved_sha256"] == queue.sha256_file(video)
    assert item["publication_id"]
    assert item["destinations"] == [{"platform": "youtube", "account_id": "primary"}]

    video.write_bytes(b"different bytes after approval")
    result = service.scan()
    assert result["needs_review"] == 1
    assert service.store.counts()["ready"] == 1  # old job remains bound to its immutable snapshot
    assert "APPROVAL_STALE" in (service.store.source_summary() or {} ) or result["blocked"] == 1


def test_stable_file_waits_before_queueing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = _prepared(tmp_path, monkeypatch, settle=30)
    result = service.scan()
    assert result["waiting_stable"] == 1
    assert service.store.counts()["ready"] == 0

    with service.store._connect() as conn:
        conn.execute(
            "UPDATE sources SET stable_since=?",
            (time.time() - 60,),
        )
    result = service.scan()
    assert result["ready_jobs_added"] == 1


def test_each_platform_account_has_independent_durable_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, _ = _prepared(
        tmp_path, monkeypatch,
        destinations=[
            {"platform": "youtube", "account_id": "channel-a"},
            {"platform": "youtube", "account_id": "channel-b"},
            {"platform": "tiktok", "account_id": "creator-c"},
        ],
    )
    result = service.scan()
    assert result["ready_jobs_added"] == 3
    assert service.store.counts()["ready"] == 3
    assert service.scan()["duplicates_ignored"] == 3
    jobs = service.store.list_jobs(20)
    assert {(job["platform"], job["account_id"]) for job in jobs} == {
        ("youtube", "channel-a"), ("youtube", "channel-b"), ("tiktok", "creator-c")
    }


def test_immutable_snapshot_is_uploaded_and_repeated_scan_does_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeAdapter()
    service, video, _ = _prepared(tmp_path, monkeypatch, publish=True, service_adapters={"youtube": fake})
    assert service.scan()["ready_jobs_added"] == 1
    original = video.read_bytes()
    video.write_bytes(b"source changed after staging")

    assert service.process_upload_one()
    current = service.store.list_jobs()[0]
    assert current["state"] == "processing_remote"
    assert Path(current["staged_path"]).read_bytes() == original
    assert fake.starts == 1

    assert service.process_remote_one()
    current = service.store.list_jobs()[0]
    assert current["state"] == "published"
    assert current["remote_id"] == "remote-123"
    assert current["remote_url"] == "https://example.test/published"
    assert fake.polls == 1
    assert service.scan()["duplicates_ignored"] == 1


def test_public_output_is_blocked_without_explicit_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    video = config.watch_dir / "public.mp4"
    video.write_bytes(b"sample")
    monkeypatch.setattr(queue, "probe_media", _media)
    sidecar = queue.approve_video(
        video, config.watch_dir,
        destinations=[{"platform": "youtube", "account_id": "primary"}],
        ai_generated=False, realistic_alteration=False,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["privacy_status"] = "public"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    service = queue.PublisherService(config, adapters={"youtube": _FakeAdapter()})
    result = service.scan()
    assert result["blocked"] == 1
    assert service.store.counts()["ready"] == 0


def test_upload_crash_without_remote_id_is_blocked_for_platforms_without_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, _ = _prepared(
        tmp_path, monkeypatch, destinations=[{"platform": "tiktok", "account_id": "creator"}],
        service_adapters={"tiktok": _FakeAdapter()},
    )
    service.scan()
    now = time.time()
    claimed = service.store.claim_upload("old-worker", now, 60)
    assert claimed is not None
    recovered = service.store.recover_expired_leases(now=now + 61)
    assert recovered["uncertain_blocked"] == 1
    item = service.store.list_jobs()[0]
    assert item["state"] == "permanent_failed"
    assert item["last_error_code"] == "UPLOAD_OUTCOME_UNKNOWN"
    with pytest.raises(ValueError, match="ambiguous remote outcome"):
        service.store.retry_job(item["id"])


def test_youtube_crash_without_remote_id_uses_idempotency_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, _ = _prepared(tmp_path, monkeypatch, publish=True)
    service.scan()
    now = time.time()
    assert service.store.claim_upload("old-worker", now, 60) is not None
    recovered = service.store.recover_expired_leases(now=now + 61)
    assert recovered["youtube_safe_retry"] == 1
    assert service.store.list_jobs()[0]["state"] == "retryable_failed"


def test_fencing_prevents_expired_worker_from_overwriting_new_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, _ = _prepared(tmp_path, monkeypatch)
    service.scan()
    now = time.time()
    first = service.store.claim_upload("worker-a", now, 60)
    assert first is not None
    with service.store._connect() as conn:
        conn.execute("UPDATE publish_jobs SET lease_expires_at=? WHERE id=?", (now - 1, first["id"]))
    second = service.store.claim_upload("worker-b", time.time(), 60)
    assert second is not None and second["id"] == first["id"]
    with pytest.raises(RuntimeError, match="lease was lost"):
        service.store.save_checkpoint(str(first["id"]), "worker-a", "stale-id", {"phase": "stale"})
    service.store.save_checkpoint(str(second["id"]), "worker-b", "fresh-id", {"phase": "fresh"})
    assert service.store.get_job(str(first["id"]))["remote_id"] == "fresh-id"


def test_manual_retry_rejects_jobs_with_remote_operation_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, _ = _prepared(tmp_path, monkeypatch, publish=True)
    service.scan()
    job = service.store.claim_upload("worker-a", time.time(), 60)
    assert job
    service.store.save_checkpoint(str(job["id"]), "worker-a", "remote-123", {"phase": "processing"})
    with service.store._connect() as conn:
        conn.execute(
            "UPDATE publish_jobs SET state='permanent_failed',last_error_code='TEMP' WHERE id=?",
            (job["id"],),
        )
    with pytest.raises(ValueError, match="remote operation ID"):
        service.store.retry_job(str(job["id"]))


def test_tiktok_chunk_upload_resumes_from_server_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"abcdefghijk")
    captured: list[tuple[str, bytes]] = []

    class Response:
        status_code = 206

    def fake_put(url, *, headers, data, **kwargs):
        captured.append((headers["Content-Range"], data))
        return Response()

    monkeypatch.setattr(adapters.requests, "put", fake_put)
    instance = adapters.TikTokAdapter()
    checkpoints: list[dict[str, object]] = []
    job = {"staged_path": str(video)}
    result = instance._upload_remaining(
        job, "fake-token", "publish-1",
        {"phase": "upload", "upload_url": "https://open-upload.tiktokapis.com/video/upload", "uploaded_bytes": 0, "total_bytes": 11, "chunk_size": 5},
        lambda remote_id, state: checkpoints.append({"id": remote_id, **state}),
        server_offset=3,
    )
    assert captured == [("bytes 3-7/11", b"defgh"), ("bytes 8-10/11", b"ijk")]
    assert result.state == "processing_remote"
    assert checkpoints[-1]["uploaded_bytes"] == 11
    assert checkpoints[-1]["phase"] == "poll"


def test_tiktok_rejects_untrusted_upload_host() -> None:
    with pytest.raises(queue.AdapterError, match="unexpected upload URL"):
        adapters.TikTokAdapter._verify_upload_url("https://attacker.example/upload")


def test_dashboard_status_does_not_expose_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRETS", "/secret/client.json")
    monkeypatch.setenv("YOUTUBE_TOKEN_PATH", "/secret/token.json")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "never-display-me")
    monkeypatch.setenv("INSTAGRAM_GRAPH_API_VERSION", "v25.0")
    configured = queue.configured_platforms()
    assert configured == {"youtube": True, "instagram": True, "tiktok": False}
    assert "never-display-me" not in json.dumps(configured)
