from __future__ import annotations

import json
from pathlib import Path

import pytest

import ai_video_factory.instagram_publisher as instagram


def _configure(monkeypatch):
    monkeypatch.setenv("AIVF_INSTAGRAM_GRAPH_API_VERSION", "v21.0")
    monkeypatch.setenv("AIVF_INSTAGRAM_USER_ID", "ig-user-123")
    monkeypatch.setenv("AIVF_INSTAGRAM_ACCESS_TOKEN", "secret-ig-token")
    monkeypatch.delenv("AIVF_INSTAGRAM_TOKEN_PATH", raising=False)
    monkeypatch.setenv("AIVF_INSTAGRAM_GRAPH_BASE", "https://graph.facebook.com")
    monkeypatch.setattr(instagram, "_access_token", lambda: "secret-ig-token")


def test_reels_publish_uses_public_url_and_persists_permalink(tmp_path, monkeypatch):
    _configure(monkeypatch)
    video = tmp_path / "story.mp4"
    video.write_bytes(b"source bytes")
    calls = []

    def fake_request(method, url, *, token, params=None, data=None):
        calls.append((method, url, params, data))
        if method == "POST" and url.endswith("/ig-user-123/media"):
            assert data["media_type"] == "REELS"
            assert data["video_url"] == "https://cdn.example.test/story.mp4"
            return {"id": "container-1"}
        if method == "GET" and url.endswith("/container-1"):
            return {"status_code": "FINISHED", "status": "ok"}
        if method == "POST" and url.endswith("/ig-user-123/media_publish"):
            assert data["creation_id"] == "container-1"
            return {"id": "media-1"}
        if method == "GET" and url.endswith("/media-1"):
            return {"id": "media-1", "permalink": "https://www.instagram.com/reel/media-1/"}
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(instagram, "_request", fake_request)
    result = instagram.publish_video(
        str(video),
        title="A Reel title",
        description="Description",
        tags=["demo"],
        privacy_status="public",
        video_url="https://cdn.example.test/story.mp4",
        idempotency_key="ig-job-1",
        idempotency_directory=str(tmp_path / "journal"),
        max_poll_seconds=0,
    )

    assert result["video_id"] == "media-1"
    assert result["url"] == "https://www.instagram.com/reel/media-1/"
    assert result["status"] == "PUBLISHED"
    assert len(calls) == 4
    journal = next((tmp_path / "journal").glob("*.json"))
    record = json.loads(journal.read_text(encoding="utf-8"))
    assert record["state"] == "published"
    assert record["result"]["video_id"] == "media-1"


def test_ambiguous_media_publish_reconciles_recent_post_before_retry(tmp_path, monkeypatch):
    _configure(monkeypatch)
    video = tmp_path / "story.mp4"
    video.write_bytes(b"source bytes")
    calls = []

    def fake_request(method, url, *, token, params=None, data=None):
        calls.append((method, url))
        if method == "POST" and url.endswith("/ig-user-123/media"):
            return {"id": "container-1"}
        if method == "GET" and url.endswith("/container-1"):
            return {"status_code": "FINISHED", "status": "ok"}
        if method == "POST" and url.endswith("/ig-user-123/media_publish"):
            raise RuntimeError("Instagram API request failed before its result could be verified")
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(instagram, "_request", fake_request)
    kwargs = dict(
        title="A Reel title",
        description="Description",
        tags=[],
        privacy_status="public",
        video_url="https://cdn.example.test/story.mp4",
        idempotency_key="ig-job-uncertain",
        idempotency_directory=str(tmp_path / "journal"),
        max_poll_seconds=0,
    )
    with pytest.raises(RuntimeError, match="publish outcome is uncertain"):
        instagram.publish_video(str(video), **kwargs)

    def matches(token, *, user_id, base, caption, created_after):
        assert caption.startswith("A Reel title")
        return [{
            "id": "remote-media-2",
            "caption": caption,
            "timestamp": "2026-10-11T12:00:00+0000",
            "permalink": "https://www.instagram.com/reel/remote-media-2/",
            "media_product_type": "REELS",
        }]

    monkeypatch.setattr(instagram, "_recent_matching_media", matches)
    result = instagram.publish_video(str(video), **kwargs)
    assert result["video_id"] == "remote-media-2"
    assert result["deduplicated"] is True
    assert len([call for call in calls if call[1].endswith("/ig-user-123/media_publish")]) == 1


def test_instagram_requires_public_https_url_and_no_unlisted_mode(tmp_path, monkeypatch):
    _configure(monkeypatch)
    video = tmp_path / "story.mp4"
    video.write_bytes(b"source bytes")
    common = dict(
        title="A title",
        description="",
        video_url="http://example.test/story.mp4",
        idempotency_key="test",
        idempotency_directory=str(tmp_path / "journal"),
    )
    with pytest.raises(ValueError, match="HTTPS"):
        instagram.publish_video(str(video), **common)
    with pytest.raises(ValueError, match="public"):
        instagram.publish_video(
            str(video),
            title="A title",
            description="",
            video_url="https://example.test/story.mp4",
            privacy_status="private",
            idempotency_key="test-2",
            idempotency_directory=str(tmp_path / "journal"),
        )


def test_meta_access_tokens_are_not_echoed_in_api_errors(monkeypatch):
    class Response:
        status_code = 400
        ok = False

        @staticmethod
        def json():
            return {"error": {"code": 190, "message": "secret-ig-token is invalid"}}

    monkeypatch.setattr(instagram.requests, "request", lambda *a, **k: Response())
    with pytest.raises(RuntimeError) as error:
        instagram._request("POST", "https://graph.facebook.com/v21.0/123/media", token="secret-ig-token")
    assert "secret-ig-token" not in str(error.value)
