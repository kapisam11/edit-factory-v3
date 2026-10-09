"""Optional YouTube publishing, playlist, disclosure and analytics integration."""
from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import stat
import time
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
REPORTING_SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]
REACH_REPORT_TYPE_ID = "channel_reach_basic_a1"
MAX_REACH_REPORT_BYTES = 50 * 1024 * 1024


def _require_google() -> None:
    try:
        import google_auth_oauthlib  # type: ignore  # noqa: F401
        import googleapiclient  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Install the YouTube extra with `pip install -e '.[youtube]'` before using publishing features.") from exc


def _credentials(client_secrets_path: str, token_path: str, scopes: Sequence[str]) -> Any:
    """Load/refresh OAuth credentials without opening an interactive browser by default."""
    _require_google()
    from google.auth.transport.requests import Request  # type: ignore
    from google.oauth2.credentials import Credentials  # type: ignore
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    token_file = Path(token_path)
    creds = (
        Credentials.from_authorized_user_file(str(token_file), scopes=list(scopes))
        if token_file.exists()
        else None
    )
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise RuntimeError(
                "YouTube OAuth refresh failed; re-authorize the saved token before publishing."
            ) from exc
    if not creds or not creds.valid:
        interactive = os.environ.get("YOUTUBE_AUTH_INTERACTIVE", "0").strip() == "1"
        if not interactive:
            raise RuntimeError(
                "YouTube OAuth credentials are unavailable or invalid and interactive auth is disabled. "
                "Authorize once with YOUTUBE_AUTH_INTERACTIVE=1, then run unattended."
            )
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



def _reporting_paths(
    client_secrets_path: Optional[str], token_path: Optional[str]
) -> tuple[str, str]:
    client = client_secrets_path or os.environ.get("YOUTUBE_CLIENT_SECRETS")
    token = token_path or os.environ.get(
        "YOUTUBE_REPORTING_TOKEN_PATH",
        os.path.expanduser("~/.config/edit-factory/youtube-reporting-token.json"),
    )
    if not client:
        raise ValueError("Provide client_secrets_path or set YOUTUBE_CLIENT_SECRETS")
    return client, token


def authorize_video_reporting_token(
    *, client_secrets_path: Optional[str] = None, token_path: Optional[str] = None
) -> str:
    """Run the one-time interactive OAuth flow for reporting-only reach metrics."""
    _require_google()
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore

    client, token = _reporting_paths(client_secrets_path, token_path)
    flow = InstalledAppFlow.from_client_secrets_file(client, scopes=REPORTING_SCOPES)
    credentials = flow.run_local_server(port=0)
    token_file = Path(token)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    try:
        os.chmod(token_file, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return str(token_file)


def aggregate_video_reach_csv(
    csv_text: str, *, video_id: str, start_date: str, end_date: str
) -> Dict[str, Any]:
    """Aggregate actual YouTube thumbnail impressions/CTR rows for one video.

    YouTube Reporting API CTR is a ratio, not Studio's Shorts "stayed to watch"
    metric. Rows outside the requested date range and malformed values are ignored.
    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    reader = csv.DictReader(io.StringIO(csv_text.lstrip("\\ufeff")))
    required = {
        "date",
        "video_id",
        "video_thumbnail_impressions",
        "video_thumbnail_impressions_ctr",
    }
    if not reader.fieldnames or not required.issubset(
        {str(name or "").strip().lower() for name in reader.fieldnames}
    ):
        return {"available": False, "reason": "missing_reach_columns"}

    daily: Dict[str, tuple[int, float]] = {}
    for row in reader:
        normalized = {str(key or "").strip().lower(): str(value or "").strip() for key, value in row.items()}
        if normalized.get("video_id") != video_id:
            continue
        try:
            row_date = date.fromisoformat(normalized.get("date", ""))
            impressions = int(float(normalized.get("video_thumbnail_impressions", "")))
            ctr = float(normalized.get("video_thumbnail_impressions_ctr", ""))
        except (TypeError, ValueError, OverflowError):
            continue
        if row_date < start or row_date > end or impressions < 0:
            continue
        if not (0.0 <= ctr <= 1.0):
            continue
        daily[row_date.isoformat()] = (impressions, ctr)

    total_impressions = sum(item[0] for item in daily.values())
    if total_impressions <= 0:
        return {
            "available": False,
            "reason": "no_reach_rows_for_video",
            "impressions": total_impressions,
            "observed_days": len(daily),
        }
    weighted_clicks = sum(impressions * ctr for impressions, ctr in daily.values())
    return {
        "available": True,
        "video_thumbnail_impressions": total_impressions,
        "video_thumbnail_impressions_ctr": round(weighted_clicks / total_impressions, 6),
        "observed_days": len(daily),
        "period_start": min(daily),
        "period_end": max(daily),
        "source": "youtube_reporting_api_channel_reach_basic_a1",
    }


def ensure_video_reach_report_job(
    *, client_secrets_path: Optional[str] = None, token_path: Optional[str] = None
) -> str:
    """Ensure YouTube's daily channel reach reporting job exists and return its ID."""
    _require_google()
    from googleapiclient.discovery import build  # type: ignore

    client, token = _reporting_paths(client_secrets_path, token_path)
    credentials = _credentials(client, token, REPORTING_SCOPES)
    reporting = build("youtubereporting", "v1", credentials=credentials, cache_discovery=False)
    page_token: Optional[str] = None
    for _ in range(20):
        kwargs: Dict[str, Any] = {"pageSize": 50}
        if page_token:
            kwargs["pageToken"] = page_token
        response = reporting.jobs().list(**kwargs).execute()
        for job in response.get("jobs") or []:
            if job.get("reportTypeId") == REACH_REPORT_TYPE_ID and job.get("id"):
                return str(job["id"])
        page_token = str(response.get("nextPageToken") or "").strip() or None
        if not page_token:
            break
    created = reporting.jobs().create(
        body={
            "reportTypeId": REACH_REPORT_TYPE_ID,
            "name": "Edit Factory channel thumbnail reach metrics",
        }
    ).execute()
    job_id = str(created.get("id") or "").strip()
    if not job_id:
        raise RuntimeError("YouTube Reporting API did not return a reach-report job ID")
    return job_id


def fetch_video_reach_metrics(
    video_id: str,
    *,
    start_date: str,
    end_date: str,
    job_id: Optional[str] = None,
    client_secrets_path: Optional[str] = None,
    token_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch real thumbnail reach/CTR from generated YouTube bulk reports.

    Reach reports are daily and may not be ready immediately; missing reports are
    returned as unavailable, never fabricated. Authorization uses a dedicated
    least-privilege reporting token so upload token grants are not silently changed.
    """
    _require_google()
    import requests

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end_date must be on or after start_date")
    client, token = _reporting_paths(client_secrets_path, token_path)
    credentials = _credentials(client, token, REPORTING_SCOPES)
    from googleapiclient.discovery import build  # type: ignore

    reporting = build("youtubereporting", "v1", credentials=credentials, cache_discovery=False)
    selected_job = str(job_id or "").strip() or ensure_video_reach_report_job(
        client_secrets_path=client, token_path=token
    )
    end_exclusive = end + timedelta(days=1)
    reports: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    for _ in range(100):
        kwargs: Dict[str, Any] = {
            "jobId": selected_job,
            "startTimeAtOrAfter": f"{start.isoformat()}T00:00:00Z",
            "startTimeBefore": f"{end_exclusive.isoformat()}T00:00:00Z",
            "pageSize": 100,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        response = reporting.jobs().reports().list(**kwargs).execute()
        reports.extend(
            item for item in (response.get("reports") or [])
            if isinstance(item, dict) and item.get("downloadUrl")
        )
        page_token = str(response.get("nextPageToken") or "").strip() or None
        if not page_token:
            break

    csv_payloads: list[str] = []
    for report in reports:
        url = str(report.get("downloadUrl") or "").strip()
        if not url:
            continue
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {credentials.token}", "Accept-Encoding": "gzip"},
            timeout=(5, 30),
            stream=True,
        )
        response.raise_for_status()
        chunks: list[bytes] = []
        byte_count = 0
        try:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                byte_count += len(chunk)
                if byte_count > MAX_REACH_REPORT_BYTES:
                    raise RuntimeError("YouTube reach report exceeds the 50 MiB safety limit")
                chunks.append(chunk)
        finally:
            response.close()
        csv_payloads.append(b"".join(chunks).decode("utf-8-sig", errors="replace"))

    if not csv_payloads:
        return {
            "available": False,
            "reason": "reach_reports_not_ready",
            "report_count": len(reports),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "source": "youtube_reporting_api_channel_reach_basic_a1",
        }

    # Reports are daily; replacing same-day entries makes repeated downloads
    # idempotent rather than double-counting duplicated report deliveries.
    merged: Dict[str, tuple[int, float]] = {}
    for payload in csv_payloads:
        result = aggregate_video_reach_csv(
            payload, video_id=video_id, start_date=start.isoformat(), end_date=end.isoformat()
        )
        if not result.get("available"):
            continue
        # Aggregate helper is intentionally pure; the per-day values are parsed
        # below so an overlapping re-delivered report can replace older data.
        reader = csv.DictReader(io.StringIO(payload.lstrip("\\ufeff")))
        for row in reader:
            normalized = {str(key or "").strip().lower(): str(value or "").strip() for key, value in row.items()}
            if normalized.get("video_id") != video_id:
                continue
            try:
                row_date = date.fromisoformat(normalized.get("date", ""))
                impressions = int(float(normalized.get("video_thumbnail_impressions", "")))
                ctr = float(normalized.get("video_thumbnail_impressions_ctr", ""))
            except (TypeError, ValueError, OverflowError):
                continue
            if start <= row_date <= end and impressions >= 0 and 0.0 <= ctr <= 1.0:
                merged[row_date.isoformat()] = (impressions, ctr)

    total_impressions = sum(value[0] for value in merged.values())
    if total_impressions <= 0:
        return {
            "available": False,
            "reason": "no_reach_rows_for_video",
            "report_count": len(reports),
            "source": "youtube_reporting_api_channel_reach_basic_a1",
        }
    weighted_clicks = sum(impressions * ctr for impressions, ctr in merged.values())
    return {
        "available": True,
        "video_thumbnail_impressions": total_impressions,
        "video_thumbnail_impressions_ctr": round(weighted_clicks / total_impressions, 6),
        "observed_days": len(merged),
        "period_start": min(merged),
        "period_end": max(merged),
        "report_count": len(reports),
        "source": "youtube_reporting_api_channel_reach_basic_a1",
    }


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


def _timestamp_epoch(value: Any) -> Optional[float]:
    """Parse a YouTube ISO-8601 timestamp into UTC epoch seconds."""
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _find_existing_video(
    youtube: Any,
    *,
    title: str,
    duration_seconds: float,
    tolerance_seconds: float = 2.5,
    published_after: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Find a matching video in the account's uploads, including private videos.

    This lookup is only for recovery of an upload with an ambiguous outcome. It
    walks the uploads playlist (rather than public search, which may omit
    private uploads) and, when supplied, only accepts videos created after the
    recorded upload-attempt time. If a bounded scan cannot reach that time
    boundary, it fails closed rather than authorizing a potentially duplicate
    upload.
    """
    channel_response = youtube.channels().list(
        part="id,contentDetails", mine=True
    ).execute()
    channels = channel_response.get("items") or []
    if not channels:
        raise RuntimeError("Cannot verify YouTube upload idempotency: no authorized channel found")
    channel = channels[0]
    uploads_playlist = str(
        ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        or ""
    )
    if not uploads_playlist:
        raise RuntimeError("Cannot verify YouTube upload idempotency: uploads playlist is unavailable")

    candidate_ids: list[str] = []
    page_token: Optional[str] = None
    cutoff_reached = published_after is None
    max_pages = 100
    for _ in range(max_pages):
        kwargs: Dict[str, Any] = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": 50,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        page = youtube.playlistItems().list(**kwargs).execute()
        items = page.get("items") or []
        if not items:
            cutoff_reached = True
            break

        reached_boundary_on_page = False
        for entry in items:
            snippet = entry.get("snippet") or {}
            published_epoch = _timestamp_epoch(snippet.get("publishedAt"))
            if (
                published_after is not None
                and published_epoch is not None
                and published_epoch < published_after
            ):
                cutoff_reached = True
                reached_boundary_on_page = True
                break

            resource_id = snippet.get("resourceId") or {}
            content_details = entry.get("contentDetails") or {}
            video_id = str(
                content_details.get("videoId") or resource_id.get("videoId") or ""
            ).strip()
            if video_id:
                candidate_ids.append(video_id)

        if reached_boundary_on_page:
            break
        page_token = str(page.get("nextPageToken") or "").strip() or None
        if not page_token:
            cutoff_reached = True
            break

    if published_after is not None and not cutoff_reached:
        raise RuntimeError(
            "Cannot safely verify YouTube upload idempotency: the uploads playlist scan "
            "did not reach the previous attempt's time boundary"
        )

    wanted = str(title).strip().casefold()
    unique_ids = list(dict.fromkeys(candidate_ids))
    for offset in range(0, len(unique_ids), 50):
        batch = unique_ids[offset : offset + 50]
        videos_response = youtube.videos().list(
            part="snippet,contentDetails,status", id=",".join(batch)
        ).execute()
        for item in videos_response.get("items") or []:
            snippet = item.get("snippet") or {}
            item_title = str(snippet.get("title") or "").strip().casefold()
            if item_title != wanted:
                continue
            if published_after is not None:
                item_epoch = _timestamp_epoch(snippet.get("publishedAt"))
                if item_epoch is None:
                    raise RuntimeError(
                        "Cannot safely verify a possible duplicate: YouTube returned no valid "
                        "publishedAt timestamp"
                    )
                if item_epoch < published_after:
                    continue
            actual_duration = _parse_duration_seconds(
                str((item.get("contentDetails") or {}).get("duration") or "")
            )
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





def _attach_uploaded_assets(
    youtube: Any,
    video_id: str,
    media_upload_type: Any,
    *,
    thumbnail_path: Optional[str],
    caption_path: Optional[str],
    caption_language: str,
) -> Dict[str, Any]:
    """Apply retry-safe thumbnail/caption steps after the video itself exists."""
    attached: Dict[str, Any] = {}
    if thumbnail_path:
        thumb = Path(thumbnail_path)
        if not thumb.is_file():
            raise FileNotFoundError(thumb)
        attached["thumbnail"] = youtube.thumbnails().set(
            videoId=video_id,
            media_body=media_upload_type(
                str(thumb),
                mimetype=mimetypes.guess_type(str(thumb))[0] or "image/jpeg",
            ),
        ).execute()
    if caption_path:
        caption = Path(caption_path)
        if not caption.is_file():
            raise FileNotFoundError(caption)
        caption_resource = youtube.captions()
        existing_items = (
            caption_resource.list(part="snippet", videoId=video_id).execute().get("items") or []
        )
        existing = next(
            (
                item for item in existing_items
                if str((item.get("snippet") or {}).get("language") or "").lower()
                == str(caption_language or "en").lower()
                and str((item.get("snippet") or {}).get("name") or "") == "Captions"
            ),
            None,
        )
        if existing:
            attached["captions"] = {
                "id": existing.get("id"),
                "already_present": True,
            }
        else:
            attached["captions"] = caption_resource.insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "language": caption_language,
                        "name": "Captions",
                        "isDraft": False,
                    }
                },
                media_body=media_upload_type(
                    str(caption),
                    mimetype=mimetypes.guess_type(str(caption))[0] or "text/vtt",
                ),
            ).execute()
    return attached


def _mark_idempotent_assets(
    idem_path: Optional[Path],
    video_id: str,
    *,
    thumbnail_path: Optional[str],
    caption_path: Optional[str],
) -> None:
    if idem_path is None:
        return
    record = _read_idempotency_record(idem_path)
    if not record or str(record.get("video_id") or "") != video_id:
        raise RuntimeError("uploaded video idempotency record changed before asset completion")
    if thumbnail_path:
        record["thumbnail_applied"] = True
    if caption_path:
        record["caption_applied"] = True
    record["assets_updated_at"] = time.time()
    _write_idempotency_record(idem_path, record)

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
    """Upload one video with optional thumbnail/captions; upload is always explicit.

    Unattended callers must have an already-authorized token.  The library will
    never open a browser unexpectedly from a daemon.
    """
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
    # Validate sidecar inputs before uploading the main video. Otherwise a bad
    # thumbnail/caption path can leave an uploaded video whose retry looks done.
    for asset_path in (thumbnail_path, caption_path):
        if asset_path and not Path(asset_path).is_file():
            raise FileNotFoundError(asset_path)
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
    previous: Optional[Dict[str, Any]] = None
    previous_attempt_started: Optional[float] = None
    if idem_path:
        previous = _read_idempotency_record(idem_path)
        if idem_path.exists() and previous is None:
            raise RuntimeError(
                "YouTube idempotency record is unreadable; refusing to risk a duplicate upload"
            )
        if previous and previous.get("video_id"):
            try:
                existing = fetch_video_statistics(
                    str(previous["video_id"]),
                    client_secrets_path=client_secrets_path,
                    token_path=token_path,
                )
            except Exception as exc:
                raise RuntimeError(
                    "A prior upload has a recorded video ID but could not be verified; "
                    "refusing to upload a duplicate"
                ) from exc
            video_id = str(previous["video_id"])
            result: Dict[str, Any] = {
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "response": existing,
                "disclosure": disclosure,
                "youtube_status": existing.get("status") or {},
                "deduplicated": True,
            }
            result.update(
                _attach_uploaded_assets(
                    youtube,
                    video_id,
                    MediaFileUpload,
                    thumbnail_path=thumbnail_path,
                    caption_path=caption_path,
                    caption_language=caption_language,
                )
            )
            _mark_idempotent_assets(
                idem_path,
                video_id,
                thumbnail_path=thumbnail_path,
                caption_path=caption_path,
            )
            return result
        if previous:
            if str(previous.get("status") or "") != "uploading":
                raise RuntimeError(
                    "YouTube idempotency record has no verified video ID; operator review is required"
                )
            try:
                previous_attempt_started = float(previous["attempt_started_at"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(
                    "YouTube idempotency record has no valid attempt timestamp; refusing duplicate upload"
                ) from exc
            try:
                existing = _find_existing_video(
                    youtube,
                    title=title,
                    duration_seconds=duration_seconds,
                    published_after=previous_attempt_started - 120.0,
                )
            except Exception as exc:
                raise RuntimeError(
                    "The previous YouTube upload outcome is uncertain and cannot be verified; "
                    "refusing another upload until verification succeeds"
                ) from exc
            if existing and existing.get("id"):
                existing_id = str(existing["id"])
                completed = {
                    **previous,
                    "status": "uploaded",
                    "video_id": existing_id,
                    "created_at": time.time(),
                    "recovered_after_error": True,
                }
                _write_idempotency_record(idem_path, completed)
                result = {
                    "video_id": existing_id,
                    "url": f"https://www.youtube.com/watch?v={existing_id}",
                    "response": existing,
                    "disclosure": disclosure,
                    "youtube_status": existing.get("status") or {},
                    "deduplicated": True,
                }
                result.update(
                    _attach_uploaded_assets(
                        youtube,
                        existing_id,
                        MediaFileUpload,
                        thumbnail_path=thumbnail_path,
                        caption_path=caption_path,
                        caption_language=caption_language,
                    )
                )
                _mark_idempotent_assets(
                    idem_path,
                    existing_id,
                    thumbnail_path=thumbnail_path,
                    caption_path=caption_path,
                )
                return result
            cooldown = 600.0
            if time.time() - previous_attempt_started < cooldown:
                raise RuntimeError(
                    "The previous YouTube upload outcome is still uncertain; refusing to risk a "
                    "duplicate for 10 minutes before the uploads playlist can be checked again"
                )

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
    attempt_started_at = time.time()
    attempt_count = int((previous or {}).get("attempt_count") or 0) + 1
    if idem_path:
        _write_idempotency_record(
            idem_path,
            {
                "status": "uploading",
                "attempt_started_at": attempt_started_at,
                "attempt_count": attempt_count,
                "title": title[:100],
                "duration_seconds": duration_seconds,
                "privacy_status": privacy_status,
            },
        )
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
            existing = _find_existing_video(
                youtube,
                title=title,
                duration_seconds=duration_seconds,
                published_after=attempt_started_at - 120.0,
            )
        except Exception as exc:
            raise RuntimeError(
                f"YouTube upload outcome is uncertain after {attempts} attempts; "
                "the idempotency record is retained and another upload is blocked until "
                "the existing upload can be verified"
            ) from exc
        if existing and existing.get("id"):
            existing_id = str(existing["id"])
            _write_idempotency_record(
                idem_path,
                {
                    "status": "uploaded",
                    "video_id": existing_id,
                    "created_at": time.time(),
                    "attempt_started_at": attempt_started_at,
                    "attempt_count": attempt_count,
                    "recovered_after_error": True,
                },
            )
            result = {
                "video_id": existing_id,
                "url": f"https://www.youtube.com/watch?v={existing_id}",
                "response": existing,
                "disclosure": disclosure,
                "youtube_status": existing.get("status") or {},
                "deduplicated": True,
            }
            result.update(
                _attach_uploaded_assets(
                    youtube,
                    existing_id,
                    MediaFileUpload,
                    thumbnail_path=thumbnail_path,
                    caption_path=caption_path,
                    caption_language=caption_language,
                )
            )
            _mark_idempotent_assets(
                idem_path,
                existing_id,
                thumbnail_path=thumbnail_path,
                caption_path=caption_path,
            )
            return result
    if response is None:
        # Keep the status=uploading marker. A retry will verify the account's
        # uploads playlist and wait out a safety cooldown before any new upload.
        raise RuntimeError(f"YouTube upload outcome is uncertain after {attempts} attempts: {upload_error}")
    video_id = response.get("id")
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
        _write_idempotency_record(
            idem_path,
            {
                "status": "uploaded",
                "video_id": str(video_id),
                "created_at": time.time(),
                "attempt_started_at": attempt_started_at,
                "attempt_count": attempt_count,
            },
        )
    result.update(
        _attach_uploaded_assets(
            youtube,
            str(video_id),
            MediaFileUpload,
            thumbnail_path=thumbnail_path,
            caption_path=caption_path,
            caption_language=caption_language,
        )
    )
    _mark_idempotent_assets(
        idem_path,
        str(video_id),
        thumbnail_path=thumbnail_path,
        caption_path=caption_path,
    )
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
    base_metrics = "views,engagedViews,likes,comments,averageViewDuration,averageViewPercentage,subscribersGained,shares"
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


__all__ = [
    "upload_video", "add_video_to_playlist", "ensure_playlist",
    "fetch_video_statistics", "fetch_video_analytics", "fetch_video_reach_metrics",
    "ensure_video_reach_report_job", "authorize_video_reporting_token",
    "aggregate_video_reach_csv", "disclosure_setting", "save_json",
    "UPLOAD_SCOPES", "ANALYTICS_SCOPES", "REPORTING_SCOPES",
]
