from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_video_factory import platform_adapters as platform
from ai_video_factory.platform_adapters import (
    InstagramReelsAdapter,
    PlatformPublishError,
    TikTokDirectPostAdapter,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, Any]:
        return self._payload


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"data": data, "error": {"code": "ok", "message": ""}}


def test_tiktok_direct_post_upload_saves_operation_and_waits_for_remote_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"local test media bytes")
    monkeypatch.setenv("AIVF_TIKTOK_TEST_ACCESS_TOKEN", "test-access-token")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    transfers: list[dict[str, str]] = []

    def request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append((method, url, kwargs.get("json")))
        if url.endswith("/creator_info/query/"):
            return FakeResponse(_ok({"privacy_level_options": ["SELF_ONLY"], "creator_username": "demo"}))
        if url.endswith("/video/init/"):
            body = kwargs["json"]
            assert body["source_info"]["source"] == "FILE_UPLOAD"
            assert body["source_info"]["video_size"] == video.stat().st_size
            assert body["source_info"]["chunk_size"] == video.stat().st_size
            assert body["source_info"]["total_chunk_count"] == 1
            return FakeResponse(
                _ok(
                    {
                        "publish_id": "publish-123",
                        "upload_url": "https://open-upload.tiktokapis.com/video/?token=opaque",
                    }
                )
            )
        if url.endswith("/status/fetch/"):
            return FakeResponse(_ok({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [987654]}))
        raise AssertionError(f"unexpected API call: {method} {url}")

    def put(url: str, **kwargs: Any) -> FakeResponse:
        transfers.append({"url": url, **kwargs["headers"]})
        assert kwargs["data"] == video.read_bytes()
        return FakeResponse({}, status_code=201)

    monkeypatch.setattr(platform.requests, "request", request)
    monkeypatch.setattr(platform.requests, "put", put)
    operations: list[tuple[str, str]] = []
    states: list[str] = []
    posts: list[tuple[str, str]] = []

    result = TikTokDirectPostAdapter().publish(
        media_path=video,
        metadata={
            "title": "A test title",
            "description": "",
            "_platform_override": {"privacy_level": "SELF_ONLY"},
            "_media_probe": {"duration_seconds": 10},
        },
        account_id="test",
        idempotency_key="stable-job-key",
        remote_operation_id=None,
        adapter_state={},
        on_operation=lambda op, _state, state: operations.append((op, state)),
        on_state=lambda _state, state: states.append(state),
        on_remote_post=lambda post, url: posts.append((post, url)),
    )

    assert result.platform == "tiktok"
    assert result.remote_operation_id == "publish-123"
    assert result.remote_post_id == "987654"
    assert result.remote_url == "https://www.tiktok.com/@demo/video/987654"
    assert operations == [("publish-123", "uploading")]
    assert "processing_remote" in states
    assert posts == [("987654", "https://www.tiktok.com/@demo/video/987654")]
    assert len(transfers) == 1
    assert transfers[0]["Content-Range"] == f"bytes 0-{video.stat().st_size - 1}/{video.stat().st_size}"
    assert not any(url.endswith("/video/init/") for _, url, _ in calls) is False


def test_tiktok_resume_queries_saved_publish_id_without_creating_another_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"local test media")
    monkeypatch.setenv("AIVF_TIKTOK_TEST_ACCESS_TOKEN", "test-access-token")
    calls: list[str] = []

    def request(method: str, url: str, **_kwargs: Any) -> FakeResponse:
        calls.append(url)
        assert method == "POST"
        assert url.endswith("/status/fetch/")
        return FakeResponse(_ok({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": []}))

    monkeypatch.setattr(platform.requests, "request", request)
    monkeypatch.setattr(
        platform.requests,
        "put",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not upload again")),
    )
    posts: list[tuple[str, str]] = []
    result = TikTokDirectPostAdapter().publish(
        media_path=video,
        metadata={"title": "Already submitted"},
        account_id="test",
        idempotency_key="same-key",
        remote_operation_id="persisted-publish-id",
        adapter_state={"publish_id": "persisted-publish-id", "stage": "submitted"},
        on_operation=lambda *_args: None,
        on_state=lambda *_args: None,
        on_remote_post=lambda post, url: posts.append((post, url)),
    )
    assert result.remote_operation_id == "persisted-publish-id"
    assert result.remote_post_id is None
    assert result.remote_url == ""
    assert calls == ["https://open.tiktokapis.com/v2/post/publish/status/fetch/"]
    assert posts == []


def test_instagram_ambiguous_media_publish_is_never_reissued_automatically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"test")
    monkeypatch.setenv("AIVF_INSTAGRAM_TEST_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("AIVF_INSTAGRAM_TEST_USER_ID", "instagram-user-123")
    monkeypatch.setenv("AIVF_INSTAGRAM_TEST_GRAPH_API_VERSION", "v26.0")
    calls: list[tuple[str, str]] = []

    def request(method: str, url: str, **_kwargs: Any) -> FakeResponse:
        calls.append((method, url))
        return FakeResponse({"id": "container-123", "status_code": "FINISHED", "status": "Finished"})

    monkeypatch.setattr(platform.requests, "request", request)
    with pytest.raises(PlatformPublishError) as caught:
        InstagramReelsAdapter().publish(
            media_path=video,
            metadata={"_platform_override": {"video_url": "https://media.example.test/clip.mp4"}},
            account_id="test",
            idempotency_key="stable-key",
            remote_operation_id="container-123",
            adapter_state={
                "container_id": "container-123",
                "stage": "publishing",
                "publish_started": True,
                "publish_started_at": 1.0,
            },
            on_operation=lambda *_args: None,
            on_state=lambda *_args: None,
            on_remote_post=lambda *_args: None,
        )

    assert caught.value.code == "AMBIGUOUS_OUTCOME"
    assert caught.value.retryable is False
    assert not any(method == "POST" and url.endswith("/media_publish") for method, url in calls)


def test_instagram_requires_public_https_media_url_before_creating_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"test")
    monkeypatch.setenv("AIVF_INSTAGRAM_TEST_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("AIVF_INSTAGRAM_TEST_USER_ID", "instagram-user-123")
    monkeypatch.setenv("AIVF_INSTAGRAM_TEST_GRAPH_API_VERSION", "v26.0")
    monkeypatch.setattr(
        platform.requests,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must reject before API request")),
    )

    with pytest.raises(PlatformPublishError) as caught:
        InstagramReelsAdapter().publish(
            media_path=video,
            metadata={"_platform_override": {"video_url": "http://127.0.0.1/clip.mp4"}},
            account_id="test",
            idempotency_key="key",
            remote_operation_id=None,
            adapter_state={},
            on_operation=lambda *_args: None,
            on_state=lambda *_args: None,
            on_remote_post=lambda *_args: None,
        )
    assert caught.value.code == "PUBLIC_MEDIA_URL_REQUIRED"
