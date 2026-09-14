"""Optional YouTube publishing, playlist, disclosure and analytics integration."""
from __future__ import annotations

import json
import mimetypes
import os
import stat
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
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube upload returned no video id: {response}")
    result: Dict[str, Any] = {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "response": response,
        "disclosure": disclosure,
        "youtube_status": status,
    }
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
            body={"snippet": {"videoId": video_id, "language": caption_language, "name": "Edit Factory captions", "isDraft": False}},
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
    return service.reports().query(ids="channel==MINE", startDate=start_date, endDate=end_date, metrics="views,likes,comments,averageViewDuration,averageViewPercentage,subscribersGained,shares", dimensions="video", filters=f"video=={video_id}").execute()


def save_json(path: str, payload: Any) -> str:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return str(output)


__all__ = ["upload_video", "add_video_to_playlist", "ensure_playlist", "fetch_video_statistics", "fetch_video_analytics", "disclosure_setting", "save_json", "UPLOAD_SCOPES", "ANALYTICS_SCOPES"]
