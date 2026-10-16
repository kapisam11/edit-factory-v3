"""Live, read-only analytics clients for supported social publishing accounts.

The API clients deliberately import only aggregate metrics for the authorized
account. Mapping those metrics to a local Edit Factory package is handled by
video_learning.py and fails closed when the original package cannot be matched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .platform_adapters import (
    InstagramReelsAdapter,
    PlatformPublishError,
    TikTokDirectPostAdapter,
    _api_error,
    _bearer,
    _request_json,
)

_TIKTOK_VIDEO_FIELDS = (
    "id,title,create_time,share_url,video_description,duration,"
    "like_count,comment_count,share_count,view_count"
)
_INSTAGRAM_MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,timestamp,permalink,"
    "like_count,comments_count"
)
_INSTAGRAM_INSIGHT_METRICS = (
    "views,reach,likes,comments,shares,saved,total_interactions"
)


def _count(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _utc_iso(value: Any) -> str:
    try:
        timestamp = int(value)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(
            microsecond=0
        ).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _valid_limit(max_videos: int) -> int:
    try:
        limit = int(max_videos)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("max_videos must be an integer between 1 and 1000") from exc
    if not 1 <= limit <= 1000:
        raise ValueError("max_videos must be an integer between 1 and 1000")
    return limit


def fetch_tiktok_video_metrics(
    *,
    account_id: str = "creator",
    max_videos: int = 100,
) -> list[dict[str, Any]]:
    """Fetch lifetime count metrics for the authorized user's recent public videos.

    TikTok's Display API requires the user-granted video.list scope. It exposes
    views/likes/comments/shares but does not expose audience retention.
    """
    limit = _valid_limit(max_videos)
    account = str(account_id or "").strip()
    if not account:
        raise ValueError("TikTok account_id must not be empty")

    adapter = TikTokDirectPostAdapter()
    token = adapter._token(account)
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    cursor: int | None = None

    while len(rows) < limit:
        body: dict[str, Any] = {"max_count": min(20, limit - len(rows))}
        if cursor is not None:
            body["cursor"] = cursor
        page = adapter._api(
            "POST",
            f"/video/list/?fields={_TIKTOK_VIDEO_FIELDS}",
            token=token,
            json_body=body,
        )
        videos = page.get("videos", page.get("video_list", []))
        if not isinstance(videos, list):
            raise PlatformPublishError(
                "TikTok video-list response did not contain a videos list",
                code="INVALID_API_RESPONSE",
                retryable=False,
            )
        for video in videos:
            if not isinstance(video, Mapping):
                continue
            video_id = str(video.get("id") or "").strip()
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)
            row: dict[str, Any] = {
                "video_id": video_id,
                "title": str(video.get("title") or video.get("video_description") or "")[:300],
                "published_at": _utc_iso(video.get("create_time")),
                "permalink": str(video.get("share_url") or ""),
                "platform": "tiktok",
            }
            for output_key, api_key in (
                ("views", "view_count"),
                ("likes", "like_count"),
                ("comments", "comment_count"),
                ("shares", "share_count"),
            ):
                metric = _count(video.get(api_key))
                if metric is not None:
                    row[output_key] = metric
            duration = _count(video.get("duration"))
            if duration is not None and duration > 0:
                row["platform_duration_seconds"] = duration
            rows.append(row)
            if len(rows) >= limit:
                break

        has_more = page.get("has_more") is True
        next_cursor_raw = page.get("cursor")
        try:
            next_cursor = int(next_cursor_raw) if next_cursor_raw is not None else None
        except (TypeError, ValueError, OverflowError):
            next_cursor = None
        if not has_more or not videos:
            break
        if next_cursor is None or str(next_cursor) in seen_cursors:
            raise PlatformPublishError(
                "TikTok pagination returned a missing or repeated cursor",
                code="INVALID_API_RESPONSE",
                retryable=False,
            )
        seen_cursors.add(str(next_cursor))
        cursor = next_cursor

    return rows[:limit]


def _insight_value(item: Mapping[str, Any]) -> Any:
    if "total_value" in item and isinstance(item.get("total_value"), Mapping):
        total = item["total_value"]
        if total.get("value") is not None:
            return total["value"]
    values = item.get("values")
    if isinstance(values, list) and values and isinstance(values[0], Mapping):
        if values[0].get("value") is not None:
            return values[0]["value"]
    return item.get("value")


def _read_instagram_insights(
    *,
    base: str,
    media_id: str,
    token: str,
) -> dict[str, Any]:
    try:
        payload = _request_json(
            "GET",
            f"{base}/{media_id}/insights",
            headers=_bearer(token),
            params={"metric": _INSTAGRAM_INSIGHT_METRICS},
            timeout=30,
            allow_redirects=False,
        )
        _api_error(payload, platform="Instagram")
    except PlatformPublishError as exc:
        # Some Graph API versions retire or rename insight metrics. Retry a
        # reduced query only for invalid-parameter failures; never mask an
        # authorization, throttling, or server error.
        if exc.code not in {"HTTP_400", "100"}:
            raise
        payload = _request_json(
            "GET",
            f"{base}/{media_id}/insights",
            headers=_bearer(token),
            params={"metric": "views"},
            timeout=30,
            allow_redirects=False,
        )
        _api_error(payload, platform="Instagram")

    data = payload.get("data")
    if not isinstance(data, list):
        raise PlatformPublishError(
            "Instagram insights response did not contain a data list",
            code="INVALID_API_RESPONSE",
            retryable=False,
        )
    result: dict[str, Any] = {}
    for item in data:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip().lower()
        if name not in {"views", "plays", "reach", "likes", "comments", "shares", "saved", "saves", "total_interactions"}:
            continue
        value = _count(_insight_value(item))
        if value is not None:
            output_name = {
                "plays": "views",
                "saves": "saves",
                "saved": "saves",
            }.get(name, name)
            result[output_name] = value
    return result


def fetch_instagram_video_metrics(
    *,
    account_id: str = "brand",
    max_videos: int = 100,
) -> list[dict[str, Any]]:
    """Fetch insights for the authorized Instagram professional account's Reels."""
    limit = _valid_limit(max_videos)
    account = str(account_id or "").strip()
    if not account:
        raise ValueError("Instagram account_id must not be empty")

    adapter = InstagramReelsAdapter()
    token, user_id, version = adapter._config(account)
    base = f"https://graph.facebook.com/{version}"
    after: str | None = None
    seen_cursors: set[str] = set()
    media_rows: list[dict[str, Any]] = []

    while len(media_rows) < limit:
        params: dict[str, Any] = {
            "fields": _INSTAGRAM_MEDIA_FIELDS,
            "limit": min(100, limit - len(media_rows)),
        }
        if after:
            params["after"] = after
        payload = _request_json(
            "GET",
            f"{base}/{user_id}/media",
            headers=_bearer(token),
            params=params,
            timeout=30,
            allow_redirects=False,
        )
        _api_error(payload, platform="Instagram")
        data = payload.get("data")
        if not isinstance(data, list):
            raise PlatformPublishError(
                "Instagram media-list response did not contain a data list",
                code="INVALID_API_RESPONSE",
                retryable=False,
            )
        for media in data:
            if not isinstance(media, Mapping):
                continue
            media_type = str(media.get("media_type") or "").upper()
            product_type = str(media.get("media_product_type") or "").upper()
            # Query video media/Reels, not image/carousel assets.
            if media_type != "VIDEO" and product_type != "REELS":
                continue
            media_id = str(media.get("id") or "").strip()
            if not media_id:
                continue
            insight = _read_instagram_insights(base=base, media_id=media_id, token=token)
            row: dict[str, Any] = {
                "video_id": media_id,
                "title": str(media.get("caption") or "")[:300],
                "published_at": str(media.get("timestamp") or ""),
                "permalink": str(media.get("permalink") or ""),
                "platform": "instagram_reels",
            }
            # Prefer insight counters, with media-edge counts as legitimate
            # fallbacks for likes/comments if a particular metric is omitted.
            if "views" in insight:
                row["views"] = insight["views"]
            for output_key in ("likes", "comments", "shares", "saves", "reach", "total_interactions"):
                if output_key in insight:
                    row[output_key] = insight[output_key]
            if "likes" not in row:
                likes = _count(media.get("like_count"))
                if likes is not None:
                    row["likes"] = likes
            if "comments" not in row:
                comments = _count(media.get("comments_count"))
                if comments is not None:
                    row["comments"] = comments
            media_rows.append(row)
            if len(media_rows) >= limit:
                break

        paging = payload.get("paging")
        cursors = paging.get("cursors") if isinstance(paging, Mapping) else None
        next_after = str(cursors.get("after") or "").strip() if isinstance(cursors, Mapping) else ""
        has_next = bool((paging or {}).get("next")) if isinstance(paging, Mapping) else False
        if not has_next or not data:
            break
        if not next_after or next_after in seen_cursors:
            raise PlatformPublishError(
                "Instagram pagination returned a missing or repeated cursor",
                code="INVALID_API_RESPONSE",
                retryable=False,
            )
        seen_cursors.add(next_after)
        after = next_after

    return media_rows[:limit]


def fetch_platform_video_metrics(
    platform: str,
    *,
    account_id: str | None = None,
    max_videos: int = 100,
) -> list[dict[str, Any]]:
    normalized = str(platform or "").strip().lower().replace("-", "_")
    if normalized == "instagram":
        normalized = "instagram_reels"
    if normalized == "tiktok":
        return fetch_tiktok_video_metrics(
            account_id=account_id or "creator",
            max_videos=max_videos,
        )
    if normalized == "instagram_reels":
        return fetch_instagram_video_metrics(
            account_id=account_id or "brand",
            max_videos=max_videos,
        )
    raise ValueError("platform must be tiktok or instagram_reels")


__all__ = [
    "fetch_instagram_video_metrics",
    "fetch_platform_video_metrics",
    "fetch_tiktok_video_metrics",
]
