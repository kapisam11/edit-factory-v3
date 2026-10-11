"""Official-platform publishing adapters with resumable/reconciled remote state.

Tokens are read from the process environment or account-specific environment
variables. They are never included in logs, job events, or dashboard responses.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

from .publishing_queue import AdapterError, AdapterUpdate

LOG = logging.getLogger(__name__)
TIKTOK_API = "https://open.tiktokapis.com"
TIKTOK_CHUNK_BYTES = 10 * 1024 * 1024
INSTAGRAM_MAX_CONTAINER_WAIT_SECONDS = 24 * 60 * 60
INSTAGRAM_PUBLISH_UNCERTAIN_SECONDS = 10 * 60

def _slug(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")[:64] or "PRIMARY"

def _env_for_account(name: str, account_id: str, default: str = "") -> str:
    value = os.environ.get(f"{name}_{_slug(account_id)}")
    return str(value if value is not None else os.environ.get(name, default)).strip()

def _request_json(method: str, url: str, *, platform: str, uncertain_on_error: bool = False, **kwargs: Any) -> dict[str, Any]:
    try:
        response = requests.request(method, url, timeout=(15, 120), allow_redirects=False, **kwargs)
    except requests.Timeout as exc:
        raise AdapterError("NETWORK_TIMEOUT", f"{platform} request timed out; remote outcome may be uncertain", retryable=True, uncertain=uncertain_on_error) from exc
    except requests.RequestException as exc:
        raise AdapterError("NETWORK_ERROR", f"{platform} network request failed ({type(exc).__name__})", retryable=True, uncertain=uncertain_on_error) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if response.status_code >= 400:
        if response.status_code in (401,):
            raise AdapterError("TOKEN_EXPIRED", f"{platform} rejected the access token; reauthorize the account", uncertain=uncertain_on_error)
        if response.status_code in (403,):
            raise AdapterError("PERMISSION_DENIED", f"{platform} denied the operation; verify app approval and account permissions", uncertain=uncertain_on_error)
        retryable = response.status_code == 408 or response.status_code == 429 or response.status_code >= 500
        raise AdapterError(
            f"{platform.upper()}_HTTP_{response.status_code}",
            f"{platform} returned HTTP {response.status_code}",
            retryable=retryable,
            uncertain=uncertain_on_error and response.status_code >= 500,
        )
    if platform == "tiktok":
        error = payload.get("error")
        if isinstance(error, dict) and str(error.get("code") or "ok").lower() not in {"ok", "0", ""}:
            code = str(error.get("code") or "API_ERROR")
            lowered = code.lower()
            if any(part in lowered for part in ("token", "auth")):
                raise AdapterError("TOKEN_EXPIRED", "TikTok authorization is invalid or expired")
            if any(part in lowered for part in ("scope", "permission", "access")):
                raise AdapterError("PERMISSION_DENIED", "TikTok app lacks the required publishing permission")
            retryable = any(part in lowered for part in ("server", "internal", "temporarily", "rate_limit", "too_many_requests"))
            raise AdapterError("TIKTOK_API_ERROR", f"TikTok API rejected the request ({code})", retryable=retryable, uncertain=uncertain_on_error and retryable)
    else:
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "GRAPH_API_ERROR")
            lowered = str(error.get("type") or "").lower()
            if response.status_code == 400 and any(part in lowered for part in ("oauth", "permission")):
                raise AdapterError("PERMISSION_DENIED", "Instagram app lacks a required permission or token scope")
            retryable = response.status_code == 429 or response.status_code >= 500
            raise AdapterError("INSTAGRAM_API_ERROR", f"Instagram Graph API rejected the request (code {code})", retryable=retryable, uncertain=uncertain_on_error and retryable)
    return payload

class _RateLimit:
    def __init__(self, minimum_interval: float) -> None:
        self.minimum_interval = minimum_interval
        self.last_call = 0.0

    def wait(self) -> None:
        remaining = self.minimum_interval - (time.monotonic() - self.last_call)
        if remaining > 0:
            time.sleep(remaining)
        self.last_call = time.monotonic()

class YouTubeAdapter:
    """Wrap the existing resumable uploader and its durable idempotency ledger."""
    safe_retry_without_remote_id = True

    @staticmethod
    def _credentials(account_id: str) -> tuple[str, str]:
        client = _env_for_account("YOUTUBE_CLIENT_SECRETS", account_id)
        token = _env_for_account("YOUTUBE_TOKEN_PATH", account_id)
        if not client or not token or not Path(client).is_file() or not Path(token).is_file():
            raise AdapterError("PLATFORM_NOT_CONFIGURED", f"YouTube credentials for account {account_id!r} are not configured")
        return client, token

    @staticmethod
    def _key(job: dict[str, Any]) -> str:
        material = "|".join((str(job["publication_id"]), "youtube", str(job["account_id"])))
        return "aivf-multi-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:48]

    def start(self, job: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None]) -> AdapterUpdate:
        from .youtube_publisher import upload_video

        meta = job["metadata"]
        client, token = self._credentials(str(job["account_id"]))
        checkpoint(None, {"phase": "youtube_uploading", "idempotency_key": self._key(job), "started_at": time.time()})
        try:
            result = upload_video(
                str(job["staged_path"]),
                title=str(meta["title"]),
                description=str(meta.get("description") or ""),
                tags=[str(tag) for tag in meta.get("tags", [])],
                privacy_status=str(meta.get("privacy_status") or "private"),
                category_id=str(meta.get("category_id") or "22"),
                client_secrets_path=client,
                token_path=token,
                ai_generated=bool(meta.get("ai_generated")),
                realistic_alteration=bool(meta.get("realistic_alteration")),
                disclosure_reviewed=bool(meta.get("disclosure_reviewed")),
                idempotency_key=self._key(job),
            )
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status in (401, 403):
                raise AdapterError("PERMISSION_DENIED", "YouTube rejected authorization or upload permission") from exc
            retryable = status in (408, 429) or (isinstance(status, int) and status >= 500) or status is None
            raise AdapterError(
                "YOUTUBE_UPLOAD_ERROR",
                "YouTube upload failed; its idempotency ledger will reconcile uncertain attempts",
                retryable=retryable,
                uncertain=True,
            ) from exc
        video_id = str(result.get("video_id") or "").strip()
        if not video_id:
            raise AdapterError("YOUTUBE_ID_MISSING", "YouTube upload returned no video ID", retryable=False, uncertain=True)
        url = str(result.get("url") or f"https://www.youtube.com/watch?v={video_id}")
        state = {"phase": "uploaded", "video_id": video_id, "uploaded_at": time.time()}
        checkpoint(video_id, state)
        return AdapterUpdate("processing_remote", video_id, url, state)

    def poll(self, job: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None]) -> AdapterUpdate:
        from .youtube_publisher import fetch_video_statistics

        meta = job["metadata"]
        client, token = self._credentials(str(job["account_id"]))
        video_id = str(job.get("remote_id") or job.get("provider_state", {}).get("video_id") or "")
        if not video_id:
            raise AdapterError("YOUTUBE_REMOTE_ID_MISSING", "YouTube job has no saved video ID; safe reconciliation cannot continue")
        try:
            remote = fetch_video_statistics(video_id, client_secrets_path=client, token_path=token)
        except Exception as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status in (401, 403):
                raise AdapterError("PERMISSION_DENIED", "YouTube status check was denied; verify token scopes") from exc
            retryable = status in (408, 429) or (isinstance(status, int) and status >= 500) or status is None
            raise AdapterError("YOUTUBE_STATUS_ERROR", "YouTube final publication status could not be verified", retryable=retryable) from exc
        status = remote.get("status") if isinstance(remote.get("status"), dict) else {}
        processing = remote.get("processingDetails") if isinstance(remote.get("processingDetails"), dict) else {}
        upload_status = str(status.get("uploadStatus") or "").lower()
        processing_status = str(processing.get("processingStatus") or "").lower()
        url = f"https://www.youtube.com/watch?v={video_id}"
        if upload_status in {"failed", "rejected"} or processing_status in {"failed", "terminated"}:
            return AdapterUpdate("permanent_failed", video_id, url, {"phase": "failed", "video_id": video_id}, "YOUTUBE_PROCESSING_FAILED", "YouTube rejected or failed processing this upload")
        if processing_status == "succeeded" or upload_status == "processed":
            result_state = {"phase": "published", "video_id": video_id, "privacy_status": str(status.get("privacyStatus") or "")}
            return AdapterUpdate("published", video_id, url, result_state)
        return AdapterUpdate("processing_remote", video_id, url, {"phase": "processing_remote", "video_id": video_id, "upload_status": upload_status, "processing_status": processing_status})

class InstagramReelsAdapter:
    """Instagram Graph API Reels adapter using Meta's resumable upload flow."""
    safe_retry_without_remote_id = False

    def __init__(self) -> None:
        self._rate_limits: dict[str, _RateLimit] = {}

    @staticmethod
    def _config(account_id: str) -> tuple[str, str, str]:
        version = os.environ.get("INSTAGRAM_GRAPH_API_VERSION", "").strip()
        token = _env_for_account("INSTAGRAM_ACCESS_TOKEN", account_id)
        if not re.fullmatch(r"v\d+\.0", version):
            raise AdapterError("PLATFORM_NOT_CONFIGURED", "set INSTAGRAM_GRAPH_API_VERSION to the Graph API version approved for your Meta app")
        if not token:
            raise AdapterError("PLATFORM_NOT_CONFIGURED", f"Instagram access token for account {account_id!r} is not configured")
        return version, token, f"https://graph.facebook.com/{version}"

    def _api(self, key: str, method: str, url: str, *, token: str, params: Optional[dict[str, Any]] = None, data: Optional[dict[str, Any]] = None, uncertain: bool = False) -> dict[str, Any]:
        limiter = self._rate_limits.setdefault(key, _RateLimit(1.0))
        limiter.wait()
        request_params = dict(params or {})
        request_params["access_token"] = token
        return _request_json(method, url, platform="instagram", uncertain_on_error=uncertain, params=request_params, data=data)

    @staticmethod
    def _upload_uri(value: Any, version: str, container_id: str) -> str:
        uri = str(value or f"https://rupload.facebook.com/ig-api-upload/{version}/{container_id}")
        parsed = urlparse(uri)
        if parsed.scheme != "https" or parsed.hostname != "rupload.facebook.com":
            raise AdapterError("INSTAGRAM_UPLOAD_URI_INVALID", "Instagram returned an unexpected resumable upload URI")
        return uri

    def _upload(self, job: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None], container_id: str, state: dict[str, Any], version: str, token: str) -> AdapterUpdate:
        video_path = Path(str(job["staged_path"]))
        size = video_path.stat().st_size
        upload_uri = self._upload_uri(state.get("upload_uri"), version, container_id)
        state = {**state, "phase": "container_created", "upload_attempted": True, "upload_started_at": time.time()}
        checkpoint(container_id, state)
        try:
            with video_path.open("rb") as handle:
                response = requests.post(
                    upload_uri,
                    headers={
                        "Authorization": f"OAuth {token}",
                        "offset": "0",
                        "file_size": str(size),
                        "Content-Length": str(size),
                        "Content-Type": "video/mp4" if video_path.suffix.lower() == ".mp4" else "video/quicktime",
                    },
                    data=handle,
                    timeout=(30, 600),
                    allow_redirects=False,
                )
        except requests.RequestException as exc:
            raise AdapterError("INSTAGRAM_UPLOAD_UNCERTAIN", f"Instagram upload request failed ({type(exc).__name__}); container status will be reconciled", retryable=True, uncertain=True) from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or not isinstance(payload, dict) or payload.get("success") is False:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise AdapterError("INSTAGRAM_TRANSFER_FAILED", f"Instagram media transfer failed with HTTP {response.status_code}; container status will be reconciled", retryable=retryable, uncertain=retryable)
        updated = {**state, "phase": "uploaded", "uploaded_at": time.time()}
        checkpoint(container_id, updated)
        return AdapterUpdate("processing_remote", container_id, None, updated)

    def start(self, job: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None]) -> AdapterUpdate:
        version, token, base = self._config(str(job["account_id"]))
        meta = job["metadata"]
        if str(meta.get("privacy_status") or "private") == "public" and os.environ.get("AIVF_PUBLISH_ALLOW_PUBLIC", "0").strip() != "1":
            raise AdapterError("PUBLIC_PUBLISH_BLOCKED", "public publishing is disabled by default")
        destination_options = meta.get("destination_options") if isinstance(meta.get("destination_options"), dict) else {}
        if destination_options.get("account_visibility_acknowledged") is not True:
            raise AdapterError("INSTAGRAM_VISIBILITY_ACK_REQUIRED", "Instagram audience visibility is determined by account settings; explicitly acknowledge the intended account audience in the destination sidecar before publishing")
        payload = self._api(
            f"instagram-container:{job['account_id']}",
            "POST",
            f"{base}/{job['account_id']}/media",
            token=token,
            data={
                "media_type": "REELS",
                "upload_type": "resumable",
                "caption": str(meta.get("description") or meta.get("title") or "")[:2200],
                "share_to_feed": "true",
            },
            uncertain=True,
        )
        container_id = str(payload.get("id") or "").strip()
        if not container_id:
            raise AdapterError("INSTAGRAM_CONTAINER_ID_MISSING", "Instagram returned no media container ID", retryable=False, uncertain=True)
        uri = self._upload_uri(payload.get("uri"), version, container_id)
        state = {
            "phase": "container_created",
            "container_id": container_id,
            "upload_uri": uri,
            "upload_attempted": False,
            "container_created_at": time.time(),
            "caption": str(meta.get("description") or meta.get("title") or "")[:2200],
        }
        checkpoint(container_id, state)
        return self._upload(job, checkpoint, container_id, state, version, token)

    def _lookup_published(self, account_id: str, token: str, base: str, state: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        caption = str(state.get("caption") or "")
        started = float(state.get("publish_started_at") or state.get("container_created_at") or 0)
        try:
            payload = self._api(f"instagram-media-list:{account_id}", "GET", f"{base}/{account_id}/media", token=token, params={"fields": "id,permalink,caption,timestamp,media_type", "limit": 50})
        except AdapterError:
            return None, None
        items = payload.get("data")
        if not isinstance(items, list):
            return None, None
        for item in items:
            if not isinstance(item, dict) or str(item.get("caption") or "") != caption:
                continue
            try:
                when = datetime.fromisoformat(str(item.get("timestamp")).replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                continue
            if when >= started - 120:
                return str(item.get("id") or "") or None, str(item.get("permalink") or "") or None
        return None, None

    def poll(self, job: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None]) -> AdapterUpdate:
        version, token, base = self._config(str(job["account_id"]))
        state = dict(job.get("provider_state") or {})
        container_id = str(state.get("container_id") or job.get("remote_id") or "")
        if not container_id:
            raise AdapterError("INSTAGRAM_CONTAINER_ID_MISSING", "Instagram container ID is not saved")
        phase = str(state.get("phase") or "container_created")
        if phase == "container_created" and not state.get("upload_attempted"):
            return self._upload(job, checkpoint, container_id, state, version, token)
        status = self._api(
            f"instagram-status:{job['account_id']}",
            "GET",
            f"{base}/{container_id}",
            token=token,
            params={"fields": "id,status_code"},
        )
        code = str(status.get("status_code") or "").upper()
        if code == "PUBLISHED":
            media_id, permalink = self._lookup_published(str(job["account_id"]), token, base, state)
            if media_id:
                final_state = {**state, "phase": "published", "published_media_id": media_id}
                return AdapterUpdate("published", media_id, permalink, final_state)
            # Container completion alone does not prove the actual published media
            # object or its permalink. Reconcile the account before reporting success.
            started = float(state.get("publish_started_at") or state.get("container_created_at") or time.time())
            if str(state.get("phase") or "") == "publishing" and time.time() - started > INSTAGRAM_PUBLISH_UNCERTAIN_SECONDS:
                return AdapterUpdate("permanent_failed", container_id, None, state, "PUBLISH_OUTCOME_UNKNOWN", "Instagram indicates a published container but the actual media object could not be reconciled; inspect the account before retrying")
            return AdapterUpdate("processing_remote", container_id, None, {**state, "phase": "publishing", "publish_started_at": started})
        if code in {"ERROR", "EXPIRED"}:
            return AdapterUpdate("retryable_failed", None, None, {}, f"INSTAGRAM_CONTAINER_{code}", f"Instagram container reached terminal status {code}; a new container may be created on retry", retryable=True, clear_remote=True)
        if code != "FINISHED":
            age = time.time() - float(state.get("container_created_at") or time.time())
            if age > INSTAGRAM_MAX_CONTAINER_WAIT_SECONDS:
                return AdapterUpdate("permanent_failed", container_id, None, state, "INSTAGRAM_PROCESSING_TIMEOUT", "Instagram container did not finish processing within 24 hours")
            return AdapterUpdate("processing_remote", container_id, None, state)
        if phase == "publishing":
            publish_started = float(state.get("publish_started_at") or time.time())
            if time.time() - publish_started > INSTAGRAM_PUBLISH_UNCERTAIN_SECONDS:
                return AdapterUpdate("permanent_failed", container_id, None, state, "PUBLISH_OUTCOME_UNKNOWN", "Instagram container is ready but the media_publish result remains ambiguous; inspect the account before retrying")
            return AdapterUpdate("processing_remote", container_id, None, state)
        publish_state = {**state, "phase": "publishing", "publish_started_at": time.time()}
        checkpoint(container_id, publish_state)
        try:
            result = self._api(
                f"instagram-publish:{job['account_id']}",
                "POST",
                f"{base}/{job['account_id']}/media_publish",
                token=token,
                data={"creation_id": container_id},
                uncertain=True,
            )
        except AdapterError:
            # The durable 'publishing' checkpoint forces future calls to reconcile
            # the container status instead of blindly calling media_publish again.
            raise
        media_id = str(result.get("id") or "").strip()
        if not media_id:
            raise AdapterError("INSTAGRAM_MEDIA_ID_MISSING", "Instagram accepted media_publish but returned no media ID", retryable=True, uncertain=True)
        final_state = {**publish_state, "phase": "published", "published_media_id": media_id}
        permalink = None
        try:
            details = self._api(f"instagram-permalink:{job['account_id']}", "GET", f"{base}/{media_id}", token=token, params={"fields": "id,permalink"})
            permalink = str(details.get("permalink") or "") or None
        except AdapterError:
            pass
        checkpoint(media_id, final_state)
        return AdapterUpdate("published", media_id, permalink, final_state)

class TikTokAdapter:
    """TikTok Content Posting API direct-publish adapter with chunk checkpoints."""
    safe_retry_without_remote_id = False

    def __init__(self) -> None:
        self._rate_limits: dict[str, _RateLimit] = {}

    def _token(self, account_id: str) -> str:
        token = _env_for_account("TIKTOK_ACCESS_TOKEN", account_id)
        if not token:
            raise AdapterError("PLATFORM_NOT_CONFIGURED", f"TikTok access token for account {account_id!r} is not configured")
        return token

    def _api(self, key: str, path: str, token: str, payload: dict[str, Any], *, uncertain: bool = False) -> dict[str, Any]:
        interval = 10.2 if path.endswith("/creator_info/query/") or path.endswith("/video/init/") else 2.1
        limiter = self._rate_limits.setdefault(key, _RateLimit(interval))
        limiter.wait()
        return _request_json(
            "POST", f"{TIKTOK_API}{path}", platform="tiktok", uncertain_on_error=uncertain,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
            json=payload,
        )

    @staticmethod
    def _caption(meta: dict[str, Any]) -> str:
        parts = [str(meta.get("title") or "").strip(), str(meta.get("description") or "").strip()]
        caption = "\n\n".join(part for part in parts if part)
        tags = meta.get("tags") or []
        if isinstance(tags, list) and tags:
            tag_string = " ".join("#" + str(tag).lstrip("#").replace(" ", "") for tag in tags if str(tag).strip())
            caption = f"{caption}\n\n{tag_string}".strip()
        # TikTok defines a 2,200 UTF-16 rune limit; astral code points count twice.
        if sum(2 if ord(char) > 0xFFFF else 1 for char in caption) > 2200:
            raise AdapterError("METADATA_LIMIT", "TikTok caption exceeds its 2,200 UTF-16 character limit")
        return caption

    @staticmethod
    def _privacy(meta: dict[str, Any]) -> str:
        privacy = str(meta.get("privacy_status") or "private").lower()
        if privacy == "private":
            return "SELF_ONLY"
        if privacy == "public":
            if os.environ.get("AIVF_PUBLISH_ALLOW_PUBLIC", "0").strip() != "1":
                raise AdapterError("PUBLIC_PUBLISH_BLOCKED", "public publishing is disabled by default")
            return "PUBLIC_TO_EVERYONE"
        raise AdapterError("PRIVACY_UNSUPPORTED", "TikTok does not provide an unlisted visibility mode; choose private or explicitly approved public")

    @staticmethod
    def _verify_upload_url(value: Any) -> str:
        url = str(value or "")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in {"open-upload.tiktokapis.com"}:
            raise AdapterError("TIKTOK_UPLOAD_URL_INVALID", "TikTok returned an unexpected upload URL")
        return url

    def _upload_remaining(self, job: dict[str, Any], token: str, publish_id: str, state: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None], server_offset: Optional[int] = None) -> AdapterUpdate:
        video = Path(str(job["staged_path"]))
        total = video.stat().st_size
        upload_url = self._verify_upload_url(state.get("upload_url"))
        offset = max(0, int(server_offset if server_offset is not None else state.get("uploaded_bytes") or 0))
        chunk_size = max(1, int(state.get("chunk_size") or TIKTOK_CHUNK_BYTES))
        while offset < total:
            with video.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(min(chunk_size, total - offset))
            if not chunk:
                raise AdapterError("LOCAL_UPLOAD_READ_FAILED", "staged media ended before the expected file size")
            end = offset + len(chunk) - 1
            try:
                response = requests.put(
                    upload_url,
                    headers={
                        "Content-Range": f"bytes {offset}-{end}/{total}",
                        "Content-Length": str(len(chunk)),
                        "Content-Type": "video/mp4",
                    },
                    data=chunk,
                    timeout=(30, 180),
                    allow_redirects=False,
                )
            except requests.Timeout as exc:
                # Server status returns its committed uploaded_bytes on the next poll.
                raise AdapterError("TIKTOK_UPLOAD_TIMEOUT", "TikTok chunk upload timed out; server byte offset will be reconciled", retryable=True, uncertain=True) from exc
            except requests.RequestException as exc:
                raise AdapterError("TIKTOK_UPLOAD_NETWORK_ERROR", f"TikTok chunk upload failed ({type(exc).__name__})", retryable=True, uncertain=True) from exc
            if response.status_code not in {200, 201, 206}:
                retryable = response.status_code == 408 or response.status_code == 429 or response.status_code >= 500
                raise AdapterError("TIKTOK_UPLOAD_FAILED", f"TikTok rejected a media chunk with HTTP {response.status_code}", retryable=retryable, uncertain=retryable)
            offset = end + 1
            state = {**state, "phase": "upload", "uploaded_bytes": offset, "total_bytes": total}
            checkpoint(publish_id, state)
        state = {**state, "phase": "poll", "uploaded_bytes": total, "total_bytes": total, "upload_completed_at": time.time()}
        checkpoint(publish_id, state)
        return AdapterUpdate("processing_remote", publish_id, None, state)

    def start(self, job: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None]) -> AdapterUpdate:
        account_id = str(job["account_id"])
        token = self._token(account_id)
        meta = job["metadata"]
        creator = self._api(f"tiktok-creator:{account_id}", "/v2/post/publish/creator_info/query/", token, {})
        creator_data = creator.get("data") if isinstance(creator.get("data"), dict) else {}
        privacy = self._privacy(meta)
        options = creator_data.get("privacy_level_options")
        if not isinstance(options, list) or privacy not in options:
            raise AdapterError("PRIVACY_UNAVAILABLE", "requested TikTok privacy level is not available to this account")
        video = Path(str(job["staged_path"]))
        total = video.stat().st_size
        media = meta.get("media") if isinstance(meta.get("media"), dict) else {}
        duration = float(media.get("duration_seconds") or 0)
        maximum = creator_data.get("max_video_post_duration_sec")
        if maximum is not None and duration > float(maximum):
            raise AdapterError("TIKTOK_DURATION_LIMIT", "video exceeds the maximum duration returned by TikTok creator_info")
        chunk_size = min(TIKTOK_CHUNK_BYTES, max(5 * 1024 * 1024, min(total, 64 * 1024 * 1024)))
        chunk_count = max(1, math.ceil(total / chunk_size))
        data = self._api(
            f"tiktok-init:{account_id}",
            "/v2/post/publish/video/init/",
            token,
            {
                "post_info": {
                    "title": self._caption(meta),
                    "privacy_level": privacy,
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "is_aigc": bool(meta.get("ai_generated")),
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": total,
                    "chunk_size": chunk_size,
                    "total_chunk_count": chunk_count,
                },
            },
            uncertain=True,
        )
        response_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        publish_id = str(response_data.get("publish_id") or "").strip()
        upload_url = self._verify_upload_url(response_data.get("upload_url"))
        if not publish_id:
            raise AdapterError("TIKTOK_PUBLISH_ID_MISSING", "TikTok did not return a publish_id", retryable=False, uncertain=True)
        state = {
            "phase": "upload",
            "publish_id": publish_id,
            "upload_url": upload_url,
            "uploaded_bytes": 0,
            "total_bytes": total,
            "chunk_size": chunk_size,
            "creator_username": str(creator_data.get("creator_username") or ""),
            "initialized_at": time.time(),
        }
        checkpoint(publish_id, state)
        return self._upload_remaining(job, token, publish_id, state, checkpoint)

    def poll(self, job: dict[str, Any], checkpoint: Callable[[Optional[str], dict[str, Any]], None]) -> AdapterUpdate:
        account_id = str(job["account_id"])
        token = self._token(account_id)
        state = dict(job.get("provider_state") or {})
        publish_id = str(state.get("publish_id") or job.get("remote_id") or "")
        if not publish_id:
            raise AdapterError("TIKTOK_PUBLISH_ID_MISSING", "TikTok publishing operation ID is not saved")
        payload = self._api(
            f"tiktok-status:{account_id}",
            "/v2/post/publish/status/fetch/",
            token,
            {"publish_id": publish_id},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        status = str(data.get("status") or "").upper()
        if status in {"PROCESSING_UPLOAD"} and str(state.get("phase") or "") in {"upload", "poll"}:
            try:
                uploaded = int(data.get("uploaded_bytes") or 0)
            except (TypeError, ValueError):
                uploaded = int(state.get("uploaded_bytes") or 0)
            total = int(state.get("total_bytes") or Path(str(job["staged_path"])).stat().st_size)
            if uploaded < total:
                return self._upload_remaining(job, token, publish_id, state, checkpoint, server_offset=uploaded)
            return AdapterUpdate("processing_remote", publish_id, None, {**state, "phase": "poll", "uploaded_bytes": uploaded})
        if status in {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD", "SEND_TO_USER_INBOX"}:
            return AdapterUpdate("processing_remote", publish_id, None, {**state, "phase": "poll", "remote_status": status})
        if status == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
            post_id = ""
            if isinstance(ids, list) and ids:
                first = ids[0]
                post_id = str(first.get("id") or "") if isinstance(first, dict) else str(first)
            username = str(state.get("creator_username") or "")
            url = f"https://www.tiktok.com/@{username}/video/{post_id}" if username and post_id else None
            saved_id = post_id or publish_id
            final = {**state, "phase": "published", "publish_id": publish_id, "post_id": post_id, "published_at": time.time()}
            checkpoint(saved_id, final)
            return AdapterUpdate("published", saved_id, url, final)
        if status == "FAILED":
            reason = str(data.get("fail_reason") or "unknown")
            if any(part in reason.lower() for part in ("server", "internal", "temporarily", "network")):
                return AdapterUpdate("retryable_failed", None, None, {}, "TIKTOK_REMOTE_TRANSIENT", f"TikTok reported a transient remote failure ({reason})", retryable=True, clear_remote=True)
            return AdapterUpdate("permanent_failed", publish_id, None, {**state, "phase": "failed", "fail_reason": reason}, "TIKTOK_REMOTE_FAILED", f"TikTok rejected or failed the post ({reason})")
        return AdapterUpdate("processing_remote", publish_id, None, {**state, "phase": "poll", "remote_status": status or "UNKNOWN"})

def build_adapters() -> dict[str, Any]:
    """Construct adapters without reading or exposing credential values."""
    return {
        "youtube": YouTubeAdapter(),
        "instagram": InstagramReelsAdapter(),
        "tiktok": TikTokAdapter(),
    }

__all__ = ["InstagramReelsAdapter", "TikTokAdapter", "YouTubeAdapter", "build_adapters"]
