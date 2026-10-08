"""Optional YouTube publishing, playlist, disclosure and analytics integration."""
from __future__ import annotations

import json
import mimetypes
import os
import stat
import time
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def _require_google() -> None:
    try:
        import google_auth_oauthlib  # type: ignore  # noqa: F401
        import googleapiclient  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Install the YouTube extra with `pip install -e '.[youtube]'` before using publishing features.") from exc


def _credentials(client_secrets_path: str, token_path: str, scopes: Sequence[str]) -> Any:
    _require_google()
    from google.auth.transport.requests import Request  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    token_file = Path(token_path)
    creds = Credentials.from_authorized_user_file(str(token_file), scopes=list(scopes)) if token_file.exists() else None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, scopes=list(scopes))
        creds = flow.run_local_server(port=0)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    try:
        os.chmod(token_file, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return creds


def _service(api: str, version: str, client_secrets_path: str, token_path: str, scopes: Sequence[str]) -> Any:
    _require_google()
    from googleapiclient.discovery import build  # type: ignore
    return build(api, version, credentials=_credentials(client_secrets_path, token_path, scopes), cache_discovery=False)


def _paths(client_secrets_path: Optional[str], token_path: Optional[str]) -> tuple[str, str]:
    client = client_secrets_path or os.environ.get("YOUTUBE_CLIENT_SECRETS")
    token = token_path or os.environ.get("YOUTUBE_TOKEN_PATH", os.path.expanduser("~/.config/edit-factory/youtube-token.json"))
    if not client:
        raise ValueError("Provide client_secrets_path or set YOUTUBE_CLIENT_SECRETS")
    return client, token



def _idempotency_path(idempotency_key: str) -> Path:
    root = Path(os.environ.get("YOUTUBE_IDEMPOTENCY_DIR", os.path.expanduser("~/.config/edit-factory/youtube-idempotency")))
    root.mkdir(parents=True, exist_ok=True)
    return root / (re.sub(r"[^a-zA-Z0-9_.-]", "_", idempotency_key) + ".json")


def _parse_duration_seconds(value: str) -> float:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?", str(value or ""))
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return float(hours or 0) * 3600.0 + float(minutes or 0) * 60.0 + float(seconds or 0.0)


def _find_existing_video(youtube: Any, *, title: str, duration_seconds: float, tolerance_seconds: float = 2.5) -> Optional[Dict[str, Any]]:
    """Find an existing upload owned by this account with the same title/duration."""
    channel_response = youtube.channels().list(part="id", mine=True).execute()
    channels = channel_response.get("items") or []
    if not channels:
        return None
    channel_id = channels[0].get("id")
    if not channel_id:
        return None
    search_response = youtube.search().list(
        part="id,snippet",
        channelId=channel_id,
        q=title[:100],
        type="video",
        maxResults=50,
    ).execute()
    ids = [str(item.get("id", {}).get("videoId") or "") for item in search_response.get("items") or []]
    ids = [item for item in ids if item]
    if not ids:
        return None
    videos_response = youtube.videos().list(part="snippet,contentDetails,status", id=",".join(ids)).execute()
    wanted = str(title).strip().casefold()
    for item in videos_response.get("items") or []:
        item_title = str((item.get("snippet") or {}).get("title") or "").strip().casefold()
        if item_title != wanted:
            continue
        actual_duration = _parse_duration_seconds(str((item.get("contentDetails") or {}).get("duration") or ""))
        if duration_seconds <= 0 or abs(actual_duration - duration_seconds) <= tolerance_seconds:
            return item
    return None


def _read_idempotency_record(path: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def _write_idempotency_record(path: Path, payload: Dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)

def disclosure_setting(*, ai_generated: bool, realistic_alteration: bool) -> Dict[str, Any]:
    """Return the disclosure decision and the YouTube API value to apply."""
    review = bool(ai_generated or realistic_alteration)
    return {
        "review_required": review,
        "ai_generated": ai_generated,
        "realistic_alteration": realistic_alteration,
        "platform_setting": bool(realistic_alteration),
        "reason": "YouTube altered/synthetic disclosure should be applied for realistic altered/synthetic media; review is required before publishing." if review else "No altered/synthetic flag was inferred from the supplied metadata.",
    }


def upload_video(
    video_path: str,
    *,
    title: str,
    description: str,
    tags: Optional[Sequence[str]] = None,
    privacy_status: str = "private",
    category_id: str = "22",
    thumbnail_path: Optional[str] = None,
    caption_path: Optional[str] = None,
    caption_language: str = "en",
    client_secrets_path: Optional[str] = None,
    token_path: Optional[str] = None,
    ai_generated: bool = False,
    realistic_alteration: bool = False,
    disclosure_reviewed: bool = False,
    idempotency_key: Optional[str] = None,
    max_upload_attempts: int = 4,
) -> Dict[str, Any]:
    """Upload one video with optional thumbnail/captions; upload is always explicit."""
    client_secrets_path, token_path = _paths(client_secrets_path, token_path)
    if privacy_status not in {"private", "public", "unlisted"}:
        raise ValueError("privacy_status must be private, public, or unlisted")
    disclosure = disclosure_setting(ai_generated=ai_generated, realistic_alteration=realistic_alteration)
    if disclosure["review_required"] and not disclosure_reviewed:
        raise ValueError("Review the AI/altered-content disclosure decision before publishing")
    _require_google()
    from googleapiclient.http import MediaFileUpload  # type: ignore
    video = Path(video_path)
    if not video.is_file():
        raise FileNotFoundError(video)
    youtube = _service("youtube", "v3", client_secrets_path, token_path, UPLOAD_SCOPES)
    duration_seconds = 0.0
    try:
        probe = __import__("subprocess").run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            check=True, capture_output=True, text=True, timeout=20,
        )
        duration_seconds = float((probe.stdout or "0").strip() or 0.0)
    except Exception:
        duration_seconds = 0.0

    idem_path = _idempotency_path(str(idempotency_key)) if idempotency_key else None
    if idem_path:
        previous = _read_idempotency_record(idem_path)
        if previous and previous.get("video_id"):
            try:
                existing = fetch_video_statistics(str(previous["video_id"]), client_secrets_path=client_secrets_path, token_path=token_path)
                return {
                    "video_id": str(previous["video_id"]),
                    "url": f"https://www.youtube.com/watch?v={previous['video_id']}",
                    "response": existing,
                    "disclosure": disclosure,
                    "youtube_status": existing.get("status") or {},
                    "deduplicated": True,
                }
            except Exception:
                pass
        try:
            existing = _find_existing_video(youtube, title=title, duration_seconds=duration_seconds)
        except Exception:
            existing = None
        if existing:
            existing_id = str(existing.get("id") or "")
            if existing_id:
                _write_idempotency_record(idem_path, {"video_id": existing_id, "created_at": time.time()})
                return {
                    "video_id": existing_id,
                    "url": f"https://www.youtube.com/watch?v={existing_id}",
                    "response": existing,
                    "disclosure": disclosure,
                    "youtube_status": existing.get("status") or {},
                    "deduplicated": True,
                }

    status = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": bool(realistic_alteration),
    }
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": list(tags or [])[:30],
            "categoryId": category_id,
        },
        "status": status,
    }
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True),
    )
    response = None
    upload_error: Optional[Exception] = None
    attempts = 0
    while response is None:
        try:
            attempts += 1
            _, response = request.next_chunk()
            upload_error = None
        except Exception as exc:
            upload_error = exc
            if attempts >= max(1, int(max_upload_attempts)):
                break
            time.sleep(min(30.0, 2.0 ** max(0, attempts - 1)))
    if response is None and idem_path:
        try:
            existing = _find_existing_video(youtube, title=title, duration_seconds=duration_seconds)
        except Exception:
            existing = None
        if existing and existing.get("id"):
            existing_id = str(existing["id"])
            _write_idempotency_record(idem_path, {"video_id": existing_id, "created_at": time.time(), "recovered_after_error": True})
            return {
                "video_id": existing_id,
                "url": f"https://www.youtube.com/watch?v={existing_id}",
                "response": existing,
                "disclosure": disclosure,
                "youtube_status": existing.get("status") or {},
                "deduplicated": True,
            }
    if response is None:
        raise RuntimeError(f"YouTube upload failed after {attempts} attempts: {upload_error}")
    if not video_id:
        raise RuntimeError(f"YouTube upload returned no video id: {response}")
    result: Dict[str, Any] = {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "response": response,
        "disclosure": disclosure,
        "youtube_status": status,
        "deduplicated": False,
    }
    if idem_path:
        _write_idempotency_record(idem_path, {"video_id": str(video_id), "created_at": time.time()})
    if thumbnail_path:
        thumb = Path(thumbnail_path)
        if not thumb.is_file():
            raise FileNotFoundError(thumb)
        result["thumbnail"] = youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb), mimetype=mimetypes.guess_type(str(thumb))[0] or "image/jpeg")).execute()
    if caption_path:
        caption = Path(caption_path)
        if not caption.is_file():
            raise FileNotFoundError(caption)
        result["captions"] = youtube.captions().insert(
            part="snippet",
            body={"snippet": {"videoId": video_id, "language": caption_language, "name": "Captions", "isDraft": False}},
            media_body=MediaFileUpload(str(caption), mimetype=mimetypes.guess_type(str(caption))[0] or "text/vtt"),
        ).execute()
    return result


def add_video_to_playlist(video_id: str, playlist_id: str, *, client_secrets_path: Optional[str] = None, token_path: Optional[str] = None) -> Dict[str, Any]:
    """Add an uploaded video to an existing playlist."""
    client_secrets_path, token_path = _paths(client_secrets_path, token_path)
    youtube = _service("youtube", "v3", client_secrets_path, token_path, UPLOAD_SCOPES)
    return youtube.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()


def ensure_playlist(title: str, *, description: str = "", privacy_status: str = "private", client_secrets_path: Optional[str] = None, token_path: Optional[str] = None) -> Dict[str, Any]:
    """Find a matching playlist or create it, then return its resource."""
    client_secrets_path, token_path = _paths(client_secrets_path, token_path)
    youtube = _service("youtube", "v3", client_secrets_path, token_path, UPLOAD_SCOPES)
    token = None
    while True:
        response = youtube.playlists().list(part="snippet,status", mine=True, maxResults=50, pageToken=token).execute()
        for item in response.get("items", []):
            if item.get("snippet", {}).get("title") == title:
                return item
        token = response.get("nextPageToken")
        if not token:
            break
    return youtube.playlists().insert(part="snippet,status", body={"snippet": {"title": title, "description": description}, "status": {"privacyStatus": privacy_status}}).execute()


def fetch_video_statistics(video_id: str, *, client_secrets_path: Optional[str] = None, token_path: Optional[str] = None) -> Dict[str, Any]:
    client_secrets_path, token_path = _paths(client_secrets_path, token_path)
    youtube = _service("youtube", "v3", client_secrets_path, token_path, UPLOAD_SCOPES + ANALYTICS_SCOPES)
    response = youtube.videos().list(part="snippet,statistics,status", id=video_id).execute()
    items = response.get("items") or []
    if not items:
        raise LookupError(f"YouTube video not found: {video_id}")
    return items[0]


def fetch_video_analytics(video_id: str, *, start_date: str, end_date: str, client_secrets_path: Optional[str] = None, token_path: Optional[str] = None) -> Dict[str, Any]:
    client_secrets_path, token_path = _paths(client_secrets_path, token_path)
    date.fromisoformat(start_date)
    date.fromisoformat(end_date)
    service = _service("youtubeAnalytics", "v2", client_secrets_path, token_path, ANALYTICS_SCOPES)
    base_metrics = "views,likes,comments,averageViewDuration,averageViewPercentage,subscribersGained,shares"
    try:
        return service.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics=base_metrics + ",estimatedRevenue",
            dimensions="video",
            filters=f"video=={video_id}",
        ).execute()
    except Exception:
        return service.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics=base_metrics,
            dimensions="video",
            filters=f"video=={video_id}",
        ).execute()


def save_json(path: str, payload: Any) -> str:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return str(output)


__all__ = ["upload_video", "add_video_to_playlist", "ensure_playlist", "fetch_video_statistics", "fetch_video_analytics", "disclosure_setting", "save_json", "UPLOAD_SCOPES", "ANALYTICS_SCOPES"]
