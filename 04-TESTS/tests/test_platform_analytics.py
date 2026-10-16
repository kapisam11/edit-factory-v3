from __future__ import annotations

from typing import Any

import pytest

from ai_video_factory import platform_analytics as analytics
from ai_video_factory.platform_adapters import PlatformPublishError


def test_tiktok_video_list_imports_counts_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeTikTok:
        def _token(self, account_id: str) -> str:
            assert account_id == "creator"
            return "token"

        def _api(self, method: str, endpoint: str, *, token: str, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
            assert method == "POST" and token == "token"
            assert endpoint.startswith("/video/list/?fields=")
            calls.append(dict(json_body or {}))
            if len(calls) == 1:
                return {"videos": [{"id": "tt-1", "create_time": 1790000000, "view_count": 1200, "like_count": 90, "comment_count": 8, "share_count": 4}], "cursor": 777, "has_more": True}
            return {"videos": [{"id": "tt-2", "view_count": 10, "like_count": 0, "comment_count": 0, "share_count": 0}], "cursor": 778, "has_more": False}

    monkeypatch.setattr(analytics, "TikTokDirectPostAdapter", FakeTikTok)
    rows = analytics.fetch_tiktok_video_metrics(account_id="creator", max_videos=21)
    assert [row["video_id"] for row in rows] == ["tt-1", "tt-2"]
    assert rows[0]["views"] == 1200
    assert rows[0]["likes"] == 90
    assert rows[0]["comments"] == 8
    assert rows[0]["shares"] == 4
    assert rows[0]["published_at"].endswith("Z")
    assert calls == [{"max_count": 20}, {"max_count": 1, "cursor": 777}]


def test_tiktok_repeated_cursor_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeTikTok:
        def _token(self, _account_id: str) -> str:
            return "token"

        def _api(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"videos": [{"id": "tt-1", "view_count": 1}], "cursor": 5, "has_more": True}

    monkeypatch.setattr(analytics, "TikTokDirectPostAdapter", FakeTikTok)
    with pytest.raises(PlatformPublishError, match="repeated cursor"):
        analytics.fetch_tiktok_video_metrics(account_id="creator", max_videos=21)


def test_instagram_reads_reel_views_and_engagement(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    class FakeInstagram:
        def _config(self, account_id: str) -> tuple[str, str, str]:
            assert account_id == "brand"
            return "token", "ig-user", "v26.0"

    def fake_request(method: str, url: str, *, params: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        assert method == "GET"
        seen.append((url, dict(params or {})))
        if url.endswith("/ig-user/media"):
            return {"data": [
                {"id": "picture-1", "media_type": "IMAGE"},
                {"id": "reel-1", "media_type": "VIDEO", "media_product_type": "REELS", "caption": "Reel", "timestamp": "2026-10-01T12:00:00+0000", "permalink": "https://www.instagram.com/reel/reel-1/", "like_count": 90, "comments_count": 8},
            ]}
        if url.endswith("/reel-1/insights"):
            return {"data": [
                {"name": "views", "values": [{"value": 1500}]},
                {"name": "likes", "values": [{"value": 90}]},
                {"name": "comments", "values": [{"value": 8}]},
                {"name": "shares", "values": [{"value": 6}]},
                {"name": "saved", "values": [{"value": 7}]},
            ]}
        raise AssertionError("unexpected API request")

    monkeypatch.setattr(analytics, "InstagramReelsAdapter", FakeInstagram)
    monkeypatch.setattr(analytics, "_request_json", fake_request)
    rows = analytics.fetch_instagram_video_metrics(account_id="brand", max_videos=10)
    assert len(rows) == 1
    assert rows[0]["video_id"] == "reel-1"
    assert rows[0]["views"] == 1500
    assert rows[0]["likes"] == 90
    assert rows[0]["comments"] == 8
    assert rows[0]["shares"] == 6
    assert rows[0]["saves"] == 7
    assert len(seen) == 2
    assert seen[0][0] == "https://graph.facebook.com/v26.0/ig-user/media"
    assert seen[1][0] == "https://graph.facebook.com/v26.0/reel-1/insights"


def test_invalid_platform_and_limit_are_rejected() -> None:
    with pytest.raises(ValueError, match="max_videos"):
        analytics.fetch_tiktok_video_metrics(max_videos=0)
    with pytest.raises(ValueError, match="platform must be"):
        analytics.fetch_platform_video_metrics("youtube")
