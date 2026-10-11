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
    video.write_bytes(b"x" * (5 * 1024 * 1024))
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
            from ai_video_factory.platform_adapters import _tiktok_chunk_plan
            chunk_size, chunk_count = _tiktok_chunk_plan(video.stat().st_size)
            assert body["source_info"]["chunk_size"] == chunk_size
            assert body["source_info"]["total_chunk_count"] == chunk_count
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


def test_tiktok_refresh_token_is_rotated_and_persisted_outside_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_video_factory import platform_auth

    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "private-state"))
    refresh_requests: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        assert url == "https://open.tiktokapis.com/v2/oauth/token/"
        refresh_requests.append(kwargs["data"])
        return FakeResponse(
            {
                "access_token": "rotated-access-token",
                "refresh_token": "rotated-refresh-token",
                "expires_in": 86400,
                "refresh_expires_in": 8640000,
                "token_type": "Bearer",
            }
        )

    monkeypatch.setattr(platform_auth.requests, "post", post)
    access = platform_auth.get_platform_access_token(
        platform="tiktok",
        account_id="creator",
        access_token="old-access-token",
        refresh_token="initial-refresh-token",
        client_key="test-client-key",
        client_secret="test-client-secret",
    )
    assert access == "rotated-access-token"
    assert refresh_requests == [
        {
            "client_key": "test-client-key",
            "client_secret": "test-client-secret",
            "grant_type": "refresh_token",
            "refresh_token": "initial-refresh-token",
        }
    ]

    second = platform_auth.get_platform_access_token(
        platform="tiktok",
        account_id="creator",
        access_token="stale-environment-token",
        refresh_token="stale-environment-refresh-token",
        client_key="test-client-key",
        client_secret="test-client-secret",
    )
    assert second == "rotated-access-token"
    assert len(refresh_requests) == 1
    db = tmp_path / "private-state" / "platform-secrets" / "platform-tokens.sqlite"
    import sqlite3

    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT access_token,refresh_token FROM platform_tokens WHERE platform='tiktok' AND account_id='creator'"
        ).fetchone()
    assert row == ("rotated-access-token", "rotated-refresh-token")
    import os
    import stat
    if os.name != "nt":
        assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(db.stat().st_mode) == 0o600


def test_expired_stored_tiktok_token_fails_closed_without_refresh_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_video_factory import platform_auth
    import sqlite3
    import time

    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    db = tmp_path / "state" / "platform-secrets" / "platform-tokens.sqlite"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE platform_tokens(platform TEXT NOT NULL,account_id TEXT NOT NULL,"
            "access_token TEXT NOT NULL,refresh_token TEXT NOT NULL DEFAULT '',"
            "expires_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL,"
            "PRIMARY KEY(platform,account_id))"
        )
        connection.execute(
            "INSERT INTO platform_tokens VALUES('tiktok','creator','expired-token','',?,?)",
            (time.time() - 60, time.time() - 100),
        )
    with pytest.raises(platform_auth.TokenRefreshError) as caught:
        platform_auth.get_platform_access_token(
            platform="tiktok",
            account_id="creator",
            access_token="expired-environment-token",
        )
    assert caught.value.code == "AUTH_REFRESH_REQUIRED"


def test_http_429_retry_after_is_preserved_for_the_durable_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse({"error": {"message": "slow down"}}, status_code=429)
    response.headers["Retry-After"] = "120"
    monkeypatch.setattr(platform.requests, "request", lambda *_args, **_kwargs: response)
    with pytest.raises(PlatformPublishError) as caught:
        platform._request_json(
            "GET",
            "https://api.example.test/resource",
            headers={"Accept": "application/json"},
        )
    assert caught.value.code == "HTTP_429"
    assert caught.value.retryable is True
    assert caught.value.retry_after_seconds == 120.0


def test_tiktok_chunk_plan_matches_current_transfer_contract() -> None:
    from ai_video_factory.platform_adapters import (
        PlatformPublishError,
        TIKTOK_MAX_CHUNK_BYTES,
        TIKTOK_MIN_CHUNK_BYTES,
        _tiktok_chunk_plan,
    )

    # TikTok's current guide explicitly permits a file smaller than 5 MiB as
    # one whole-file request; the 5 MiB minimum applies to multi-part chunks.
    small_size = TIKTOK_MIN_CHUNK_BYTES - 1
    assert _tiktok_chunk_plan(small_size) == (small_size, 1)
    with pytest.raises(PlatformPublishError) as caught:
        _tiktok_chunk_plan(0)
    assert caught.value.code == "MEDIA_SIZE_LIMIT"

    for size in (5 * 1024 * 1024, 65 * 1024 * 1024, 65 * 1024 * 1024 + 1, 128 * 1024 * 1024 + 7):
        chunk_size, chunks = _tiktok_chunk_plan(size)
        assert TIKTOK_MIN_CHUNK_BYTES <= chunk_size <= TIKTOK_MAX_CHUNK_BYTES
        assert chunks == size // chunk_size
        assert 1 <= chunks <= 1000
        assert (chunks - 1) * chunk_size < size <= chunks * chunk_size + chunks


def test_tiktok_resume_queries_saved_publish_id_without_creating_another_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * (5 * 1024 * 1024))
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


def test_youtube_waits_for_processing_and_persists_remote_id_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_video_factory import youtube_publisher
    from ai_video_factory.platform_adapters import YouTubeAdapter

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"youtube test media")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRETS", "fake-client-secrets.json")
    monkeypatch.setenv("YOUTUBE_TOKEN_PATH", str(tmp_path / "token.json"))
    monkeypatch.setenv("AIVF_YOUTUBE_REMOTE_WAIT_SECONDS", "5")
    monkeypatch.setenv("AIVF_YOUTUBE_POLL_SECONDS", "1")
    monkeypatch.setattr(
        youtube_publisher,
        "upload_video",
        lambda *_args, **_kwargs: {
            "video_id": "youtube-video-123",
            "url": "https://www.youtube.com/watch?v=youtube-video-123",
        },
    )

    class FakeRequest:
        def execute(self) -> dict[str, Any]:
            return {
                "items": [
                    {
                        "status": {
                            "uploadStatus": "processed",
                            "privacyStatus": "private",
                        },
                        "processingDetails": {"processingStatus": "succeeded"},
                    }
                ]
            }

    class FakeVideos:
        def list(self, **kwargs: Any) -> FakeRequest:
            assert kwargs["id"] == "youtube-video-123"
            assert kwargs["part"] == "processingDetails,status"
            return FakeRequest()

    class FakeService:
        def videos(self) -> FakeVideos:
            return FakeVideos()

    monkeypatch.setattr(youtube_publisher, "_service", lambda *_args, **_kwargs: FakeService())
    saved_operations: list[tuple[str, str]] = []
    saved_posts: list[tuple[str, str]] = []
    result = YouTubeAdapter().publish(
        media_path=video,
        metadata={"title": "Test", "privacy_status": "private"},
        account_id="default",
        idempotency_key="stable-youtube-key",
        remote_operation_id=None,
        adapter_state={},
        on_operation=lambda op, _state, state: saved_operations.append((op, state)),
        on_state=lambda _state, _state_name: None,
        on_remote_post=lambda post_id, url: saved_posts.append((post_id, url)),
    )
    assert result.remote_operation_id == "youtube-video-123"
    assert result.remote_post_id == "youtube-video-123"
    assert result.remote_url == "https://www.youtube.com/watch?v=youtube-video-123"
    assert saved_operations == [("youtube-video-123", "processing_remote")]
    assert saved_posts == [("youtube-video-123", "https://www.youtube.com/watch?v=youtube-video-123")]


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


def test_tiktok_public_post_without_public_id_remains_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * (5 * 1024 * 1024))
    monkeypatch.setenv("AIVF_TIKTOK_TEST_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("AIVF_TIKTOK_REMOTE_WAIT_SECONDS", "1")
    monkeypatch.setenv("AIVF_TIKTOK_POLL_SECONDS", "1")

    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(platform.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(platform.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        platform.requests,
        "request",
        lambda *_args, **_kwargs: FakeResponse(
            _ok({"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": []})
        ),
    )

    with pytest.raises(PlatformPublishError) as caught:
        TikTokDirectPostAdapter().publish(
            media_path=video,
            metadata={},
            account_id="test",
            idempotency_key="public-post-key",
            remote_operation_id="persisted-public-publish-id",
            adapter_state={
                "publish_id": "persisted-public-publish-id",
                "stage": "submitted",
                "privacy_level": "PUBLIC_TO_EVERYONE",
            },
            on_operation=lambda *_args: None,
            on_state=lambda *_args: None,
            on_remote_post=lambda *_args: None,
        )
    assert caught.value.code == "PUBLICATION_VISIBILITY_UNCONFIRMED"
    assert caught.value.retryable is True
    assert caught.value.uncertain is True

def test_youtube_visibility_mismatch_is_not_reported_as_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_video_factory import youtube_publisher
    from ai_video_factory.platform_adapters import PlatformPublishError, YouTubeAdapter

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"visibility fixture")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRETS", "fake-client-secrets.json")
    monkeypatch.setenv("YOUTUBE_TOKEN_PATH", str(tmp_path / "token.json"))
    monkeypatch.setattr(
        youtube_publisher,
        "upload_video",
        lambda *_args, **_kwargs: {
            "video_id": "youtube-private-video",
            "url": "https://www.youtube.com/watch?v=youtube-private-video",
        },
    )

    class FakeRequest:
        def execute(self) -> dict[str, Any]:
            return {
                "items": [
                    {
                        "status": {"uploadStatus": "processed", "privacyStatus": "private"},
                        "processingDetails": {"processingStatus": "succeeded"},
                    }
                ]
            }

    class FakeVideos:
        def list(self, **_kwargs: Any) -> FakeRequest:
            return FakeRequest()

    class FakeService:
        def videos(self) -> FakeVideos:
            return FakeVideos()

    monkeypatch.setattr(youtube_publisher, "_service", lambda *_args, **_kwargs: FakeService())
    saved_ids: list[str] = []
    with pytest.raises(PlatformPublishError) as caught:
        YouTubeAdapter().publish(
            media_path=video,
            metadata={"title": "Test", "privacy_status": "public"},
            account_id="default",
            idempotency_key="stable-youtube-key",
            remote_operation_id=None,
            adapter_state={},
            on_operation=lambda *_args: None,
            on_state=lambda *_args: None,
            on_remote_post=lambda post_id, _url: saved_ids.append(post_id),
        )
    assert caught.value.code == "REMOTE_VISIBILITY_MISMATCH"
    assert caught.value.retryable is False
    assert saved_ids == ["youtube-private-video"]

def test_tiktok_chunk_transfer_tracks_server_acknowledgements_and_stops_on_201(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_video_factory.platform_adapters import (
        TIKTOK_MAX_CHUNK_BYTES,
        TikTokDirectPostAdapter,
        _tiktok_chunk_plan,
    )

    size = 65 * 1024 * 1024
    video = tmp_path / "chunked.mp4"
    video.write_bytes(b"x" * size)
    chunk_size, chunk_count = _tiktok_chunk_plan(size)
    assert chunk_count == 2
    responses = [
        FakeResponse({}, status_code=206),
        FakeResponse({}, status_code=201),
    ]
    responses[0].headers["Content-Range"] = f"bytes 0-{chunk_size - 1}/{size}"
    responses[1].headers["Content-Range"] = f"bytes 0-{size - 1}/{size}"
    sent: list[dict[str, Any]] = []
    persisted: list[int] = []

    def put(_url: str, **kwargs: Any) -> FakeResponse:
        sent.append(kwargs)
        return responses[len(sent) - 1]

    monkeypatch.setattr(platform.requests, "put", put)
    state: dict[str, Any] = {"stage": "uploading", "uploaded_bytes": 0}
    final_offset = TikTokDirectPostAdapter._upload_remaining(
        video,
        "https://open-upload.tiktokapis.com/video/?opaque=token",
        "not-used-for-upload-auth",
        0,
        state,
        lambda updated, _state: persisted.append(int(updated["uploaded_bytes"])),
    )

    assert final_offset == size
    assert state["uploaded_bytes"] == size
    assert persisted == [chunk_size, size]
    assert len(sent) == 2
    assert sent[0]["headers"]["Content-Range"] == f"bytes 0-{chunk_size - 1}/{size}"
    assert sent[1]["headers"]["Content-Range"] == f"bytes {chunk_size}-{size - 1}/{size}"
    assert len(sent[0]["data"]) == chunk_size
    assert len(sent[1]["data"]) == size - chunk_size
    assert len(sent[0]["data"]) <= TIKTOK_MAX_CHUNK_BYTES
    assert responses[-1].status_code == 201


def test_tiktok_reconciles_partial_upload_from_server_content_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_video_factory.platform_adapters import TikTokDirectPostAdapter

    size = 65 * 1024 * 1024
    uploaded = 5 * 1024 * 1024
    video = tmp_path / "resume.mp4"
    video.write_bytes(b"x" * size)
    calls: list[dict[str, Any]] = []

    def put(_url: str, **kwargs: Any) -> FakeResponse:
        calls.append(kwargs)
        response = FakeResponse({}, status_code=201)
        response.headers["Content-Range"] = f"bytes 0-{size - 1}/{size}"
        return response

    monkeypatch.setattr(platform.requests, "put", put)
    state: dict[str, Any] = {"stage": "uploading", "uploaded_bytes": uploaded}
    final_offset = TikTokDirectPostAdapter._upload_remaining(
        video,
        "https://open-upload.tiktokapis.com/video/?opaque=token",
        "not-used-for-upload-auth",
        uploaded,
        state,
        lambda *_args: None,
    )
    assert final_offset == size
    assert len(calls) == 1
    assert calls[0]["headers"]["Content-Range"] == f"bytes {uploaded}-{size - 1}/{size}"


def test_tiktok_rejects_unrecognized_success_codes_in_media_transfer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_video_factory.platform_adapters import TikTokDirectPostAdapter

    video = tmp_path / "unsupported-transfer-status.mp4"
    video.write_bytes(b"x" * (5 * 1024 * 1024))
    monkeypatch.setattr(platform.requests, "put", lambda *_args, **_kwargs: FakeResponse({}, status_code=308))
    with pytest.raises(PlatformPublishError) as caught:
        TikTokDirectPostAdapter._upload_remaining(
            video,
            "https://open-upload.tiktokapis.com/video/?opaque=token",
            "not-used-for-upload-auth",
            0,
            {},
            lambda *_args: None,
        )
    assert caught.value.code == "UPLOAD_HTTP_308"
    assert caught.value.retryable is False
