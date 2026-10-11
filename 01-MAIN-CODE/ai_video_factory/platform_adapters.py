"""Platform API adapters for durable, independently retried video publication.

The module never discovers credentials from a sidecar. Account secrets are loaded
from environment variables and are never included in result objects or log text.
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from .platform_auth import TokenRefreshError, get_platform_access_token


@dataclass(frozen=True)
class PublishResult:
    platform: str
    remote_operation_id: str | None
    remote_post_id: str | None
    remote_url: str = ""


class PlatformPublishError(RuntimeError):
    """Redacted platform failure classification for the durable retry queue."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        uncertain: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.uncertain = uncertain
        self.retry_after_seconds = (
            max(0.0, min(float(retry_after_seconds), 86400.0))
            if retry_after_seconds is not None
            else None
        )


OperationCallback = Callable[[str, dict[str, Any], str], None]
StateCallback = Callable[[dict[str, Any], str], None]
RemotePostCallback = Callable[[str, str], None]


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _account_env_name(platform: str, account_id: str, field: str) -> str:
    alias = "".join(char if char.isalnum() else "_" for char in account_id.upper()).strip("_")
    if not alias:
        raise PlatformPublishError(
            "destination account identifier is invalid",
            code="INVALID_ACCOUNT",
            retryable=False,
        )
    return f"AIVF_{platform.upper()}_{alias}_{field.upper()}"


def _account_setting(
    platform: str,
    account_id: str,
    field: str,
    *,
    legacy_name: str | None = None,
    default: str = "",
) -> str:
    specific = os.environ.get(_account_env_name(platform, account_id, field), "").strip()
    if specific:
        return specific
    if legacy_name and account_id.lower() in {"default", "main"}:
        legacy = os.environ.get(legacy_name, "").strip()
        if legacy:
            return legacy
    return default


def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 30,
    allow_redirects: bool = True,
) -> dict[str, Any]:
    """Call an API without leaking signed URLs or bearer tokens in exceptions."""
    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            data=data,
            json=json_body,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )
    except requests.RequestException as exc:
        raise PlatformPublishError(
            f"platform network request failed ({type(exc).__name__})",
            code="NETWORK_ERROR",
            retryable=True,
        ) from exc

    retry_after_seconds: float | None = None
    retry_after_header = response.headers.get("Retry-After")
    if retry_after_header:
        try:
            retry_after_seconds = max(0.0, min(float(retry_after_header), 86400.0))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(retry_after_header)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                retry_after_seconds = max(
                    0.0,
                    min((retry_at - datetime.now(timezone.utc)).total_seconds(), 86400.0),
                )
            except (TypeError, ValueError, OverflowError):
                retry_after_seconds = None

    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if not response.ok:
        api_error = payload.get("error")
        detail = ""
        if isinstance(api_error, dict):
            detail = str(api_error.get("message") or api_error.get("code") or "")
        elif isinstance(payload.get("message"), str):
            detail = str(payload["message"])
        detail = " ".join(detail.split())[:300]
        retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
        code = f"HTTP_{response.status_code}"
        safe_detail = f": {detail}" if detail else ""
        raise PlatformPublishError(
            f"platform returned HTTP {response.status_code}{safe_detail}",
            code=code,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
        )

    return payload


def _api_error(payload: dict[str, Any], *, platform: str) -> None:
    """Raise from an HTTP-200 API envelope that contains a provider-level failure."""
    error = payload.get("error")
    if not isinstance(error, dict):
        return
    raw_code = str(error.get("code") or "").strip()
    if not raw_code or raw_code.lower() == "ok":
        return
    message = " ".join(str(error.get("message") or raw_code).split())[:300]
    retryable = raw_code.lower() in {
        "rate_limit_exceeded",
        "internal_error",
        "temporarily_unavailable",
        "server_error",
        "network_error",
    } or raw_code.startswith(("5", "429"))
    raise PlatformPublishError(
        f"{platform} API rejected the request ({raw_code}): {message}",
        code=raw_code.upper(),
        retryable=retryable,
    )


class PlatformAdapter:
    platform = "unknown"

    def publish(
        self,
        *,
        media_path: Path,
        metadata: dict[str, Any],
        account_id: str,
        idempotency_key: str,
        remote_operation_id: str | None,
        adapter_state: dict[str, Any],
        on_operation: OperationCallback,
        on_state: StateCallback,
        on_remote_post: RemotePostCallback,
    ) -> PublishResult:
        raise NotImplementedError


class YouTubeAdapter(PlatformAdapter):
    platform = "youtube"

    def publish(
        self,
        *,
        media_path: Path,
        metadata: dict[str, Any],
        account_id: str,
        idempotency_key: str,
        remote_operation_id: str | None,
        adapter_state: dict[str, Any],
        on_operation: OperationCallback,
        on_state: StateCallback,
        on_remote_post: RemotePostCallback,
    ) -> PublishResult:
        client_secrets = _account_setting(
            self.platform,
            account_id,
            "client_secrets",
            legacy_name="YOUTUBE_CLIENT_SECRETS",
        )
        token_path = _account_setting(
            self.platform,
            account_id,
            "token_path",
            legacy_name="YOUTUBE_TOKEN_PATH",
            default=str(Path.home() / ".config" / "edit-factory" / f"youtube-token-{account_id}.json"),
        )
        if not client_secrets:
            raise PlatformPublishError(
                f"YouTube credentials are not configured for account {account_id!r}",
                code="MISSING_CREDENTIALS",
                retryable=False,
            )
        try:
            from . import youtube_publisher

            result = youtube_publisher.upload_video(
                str(media_path),
                title=str(metadata.get("title") or media_path.stem)[:100],
                description=str(metadata.get("description") or "")[:5000],
                tags=[str(tag) for tag in metadata.get("tags", []) if isinstance(tag, str)][:30],
                privacy_status=str(metadata.get("privacy_status") or "private"),
                category_id=str(metadata.get("category_id") or "22"),
                thumbnail_path=str(metadata["thumbnail_path"]) if metadata.get("thumbnail_path") else None,
                caption_path=str(metadata["caption_path"]) if metadata.get("caption_path") else None,
                caption_language=str(metadata.get("caption_language") or "en"),
                client_secrets_path=client_secrets,
                token_path=token_path,
                ai_generated=metadata.get("ai_generated") is True,
                realistic_alteration=metadata.get("realistic_alteration") is True,
                disclosure_reviewed=metadata.get("disclosure_reviewed") is True,
                idempotency_key=idempotency_key,
            )
        except PlatformPublishError:
            raise
        except Exception as exc:
            # upload_video's durable idempotency record reconciles uncertain inserts.
            retryable = _looks_transient(str(exc), type(exc).__name__)
            raise PlatformPublishError(
                f"YouTube upload failed ({type(exc).__name__})",
                code="YOUTUBE_UPLOAD_FAILED",
                retryable=retryable,
                uncertain=retryable,
            ) from exc

        post_id = str(result.get("video_id") or "")
        if not post_id:
            raise PlatformPublishError(
                "YouTube upload returned no verified video ID",
                code="MISSING_REMOTE_ID",
                retryable=True,
                uncertain=True,
            )
        url = str(result.get("url") or f"https://www.youtube.com/watch?v={post_id}")
        # Persist the ID before waiting for remote transcoding. If polling times
        # out or the worker restarts, the durable YouTube idempotency record and
        # this queue entry both point at the same remote video.
        on_operation(
            post_id,
            {"stage": "processing_remote", "video_id": post_id},
            "processing_remote",
        )
        on_remote_post(post_id, url)
        remote_status = _verify_youtube_processing(
            post_id,
            client_secrets_path=client_secrets,
            token_path=token_path,
        )
        result["verified_remote_status"] = remote_status
        return PublishResult("youtube", post_id, post_id, url)


def _looks_transient(message: str, exception_name: str) -> bool:
    lowered = f"{exception_name} {message}".lower()
    return any(
        marker in lowered
        for marker in (
            "timeout",
            "timed out",
            "connection",
            "temporarily unavailable",
            "rate limit",
            "429",
            "500",
            "502",
            "503",
            "504",
            "reset by peer",
        )
    )


def _redacted_message(exc: Exception) -> str:
    # Avoid rendering arbitrary request exception strings, which may contain
    # complete signed URLs or query parameters. Provider errors are handled by
    # _request_json and contain only the provider's message.
    if isinstance(exc, requests.RequestException):
        return type(exc).__name__
    value = " ".join(str(exc).split())
    return value[:240] or type(exc).__name__


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _require_https_public_url(value: Any, *, field: str) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower()
    except ValueError:
        host = ""
        parsed = urlparse("")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or host in {"localhost", "localhost.localdomain"}
        or host.endswith(".localhost")
    ):
        raise PlatformPublishError(
            f"{field} must be a public HTTPS URL accessible by the platform",
            code="PUBLIC_MEDIA_URL_REQUIRED",
            retryable=False,
        )
    try:
        import ipaddress

        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise PlatformPublishError(
                f"{field} must not point to a private or reserved IP address",
                code="PUBLIC_MEDIA_URL_REQUIRED",
                retryable=False,
            )
    except ValueError:
        pass
    return raw


TIKTOK_MIN_CHUNK_BYTES = 5 * 1024 * 1024
TIKTOK_MAX_CHUNK_BYTES = 64 * 1024 * 1024


def _tiktok_chunk_plan(file_size: int) -> tuple[int, int]:
    """Choose chunks that respect TikTok's 5–64 MiB per-chunk contract."""
    size = int(file_size)
    if size < TIKTOK_MIN_CHUNK_BYTES:
        raise PlatformPublishError(
            "TikTok Direct Post requires a video of at least 5 MiB for file upload",
            code="MEDIA_SIZE_LIMIT",
            retryable=False,
        )
    if size > 4 * 1024 * 1024 * 1024:
        raise PlatformPublishError(
            "TikTok Direct Post file upload is limited to 4 GiB",
            code="MEDIA_SIZE_LIMIT",
            retryable=False,
        )
    chunk_count = (size + TIKTOK_MAX_CHUNK_BYTES - 1) // TIKTOK_MAX_CHUNK_BYTES
    chunk_size = (size + chunk_count - 1) // chunk_count
    if not TIKTOK_MIN_CHUNK_BYTES <= chunk_size <= TIKTOK_MAX_CHUNK_BYTES:
        raise PlatformPublishError(
            "video size cannot be represented using TikTok's supported upload chunk sizes",
            code="MEDIA_SIZE_LIMIT",
            retryable=False,
        )
    return chunk_size, chunk_count


def _wait_settings(platform: str) -> tuple[float, float]:
    try:
        timeout = float(os.environ.get(f"AIVF_{platform.upper()}_REMOTE_WAIT_SECONDS", "180"))
    except ValueError:
        timeout = 180.0
    try:
        interval = float(os.environ.get(f"AIVF_{platform.upper()}_POLL_SECONDS", "5"))
    except ValueError:
        interval = 5.0
    return max(1.0, min(timeout, 1800.0)), max(1.0, min(interval, 60.0))


def _verify_youtube_processing(
    video_id: str,
    *,
    client_secrets_path: str,
    token_path: str,
) -> dict[str, Any]:
    """Poll the owner-only YouTube processing state before marking a job published."""
    from .youtube_publisher import UPLOAD_SCOPES, _service

    timeout, interval = _wait_settings("youtube")
    deadline = time.monotonic() + timeout
    service = _service(
        "youtube",
        "v3",
        client_secrets_path,
        token_path,
        UPLOAD_SCOPES,
    )
    while True:
        try:
            response = service.videos().list(
                part="processingDetails,status",
                id=video_id,
            ).execute()
        except Exception as exc:
            raise PlatformPublishError(
                f"YouTube processing-status lookup failed ({type(exc).__name__})",
                code="REMOTE_STATUS_LOOKUP_FAILED",
                retryable=True,
                uncertain=True,
            ) from exc
        items = response.get("items") or []
        if not items:
            raise PlatformPublishError(
                "YouTube did not return the uploaded video while verifying processing",
                code="REMOTE_VIDEO_NOT_FOUND",
                retryable=True,
                uncertain=True,
            )
        item = items[0]
        processing = item.get("processingDetails") or {}
        status = item.get("status") or {}
        processing_state = str(processing.get("processingStatus") or "").lower()
        upload_state = str(status.get("uploadStatus") or "").lower()
        if processing_state == "failed" or upload_state in {"failed", "rejected", "deleted"}:
            reason = str(
                processing.get("processingFailureReason")
                or status.get("failureReason")
                or status.get("rejectionReason")
                or "remote processing failed"
            )[:180]
            raise PlatformPublishError(
                f"YouTube rejected or failed to process the upload ({reason})",
                code="YOUTUBE_PROCESSING_FAILED",
                retryable=False,
                uncertain=False,
            )
        if processing_state == "succeeded" or upload_state == "processed":
            return {
                "processing_status": processing_state or "succeeded",
                "upload_status": upload_state,
                "privacy_status": str(status.get("privacyStatus") or ""),
                "failure_reason": "",
            }
        if time.monotonic() >= deadline:
            raise PlatformPublishError(
                "YouTube has not finished processing the uploaded video; the saved video ID will be checked again",
                code="REMOTE_PROCESSING",
                retryable=True,
                uncertain=True,
            )
        time.sleep(interval)


class InstagramReelsAdapter(PlatformAdapter):
    platform = "instagram"

    def _config(self, account_id: str) -> tuple[str, str, str]:
        raw_token = _account_setting(
            self.platform,
            account_id,
            "access_token",
            legacy_name="INSTAGRAM_ACCESS_TOKEN",
        )
        user_id = _account_setting(
            self.platform,
            account_id,
            "user_id",
            legacy_name="INSTAGRAM_USER_ID",
        )
        version = _account_setting(
            self.platform,
            account_id,
            "graph_api_version",
            legacy_name="INSTAGRAM_GRAPH_API_VERSION",
        )
        app_id = _account_setting(self.platform, account_id, "app_id")
        app_secret = _account_setting(self.platform, account_id, "app_secret")
        if not raw_token or not user_id or not version:
            raise PlatformPublishError(
                f"Instagram account {account_id!r} requires access token, professional account ID, and pinned Graph API version",
                code="MISSING_CREDENTIALS",
                retryable=False,
            )
        if not version.startswith("v") or any(ch not in "v0123456789." for ch in version):
            raise PlatformPublishError(
                "Instagram Graph API version must be a pinned version such as v26.0",
                code="INVALID_CONFIGURATION",
                retryable=False,
            )
        try:
            token = get_platform_access_token(
                platform="instagram",
                account_id=account_id,
                access_token=raw_token,
                app_id=app_id,
                app_secret=app_secret,
                graph_api_version=version,
            )
        except TokenRefreshError as exc:
            raise PlatformPublishError(
                str(exc),
                code=exc.code,
                retryable=exc.retryable,
                uncertain=exc.retryable,
            ) from exc
        return token, user_id, version

    def publish(
        self,
        *,
        media_path: Path,
        metadata: dict[str, Any],
        account_id: str,
        idempotency_key: str,
        remote_operation_id: str | None,
        adapter_state: dict[str, Any],
        on_operation: OperationCallback,
        on_state: StateCallback,
        on_remote_post: RemotePostCallback,
    ) -> PublishResult:
        token, user_id, version = self._config(account_id)
        headers = _bearer(token)
        base = f"https://graph.facebook.com/{version}"
        overrides = metadata.get("_platform_override")
        target_metadata = overrides if isinstance(overrides, dict) else {}
        video_url = _require_https_public_url(
            target_metadata.get("video_url") or metadata.get("instagram_video_url"),
            field="Instagram video_url",
        )
        caption = str(target_metadata.get("caption") or metadata.get("description") or metadata.get("title") or "")
        if len(caption) > 2200:
            raise PlatformPublishError(
                "Instagram Reels caption exceeds the configured 2200-character safety limit",
                code="INVALID_METADATA",
                retryable=False,
            )

        state = dict(adapter_state)
        container_id = str(remote_operation_id or state.get("container_id") or "")
        if not container_id:
            params = {
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": str(bool(target_metadata.get("share_to_feed", True))).lower(),
            }
            payload = _request_json(
                "POST", f"{base}/{user_id}/media", headers=headers, data=params, timeout=40
            )
            _api_error(payload, platform="Instagram")
            container_id = str((payload.get("id") if isinstance(payload, dict) else "") or "")
            if not container_id:
                raise PlatformPublishError(
                    "Instagram did not return a media container ID",
                    code="MISSING_REMOTE_ID",
                    retryable=True,
                    uncertain=True,
                )
            state.update(
                {
                    "container_id": container_id,
                    "stage": "container_created",
                    "publish_started": False,
                    "publish_started_at": None,
                }
            )
            on_operation(container_id, state, "processing_remote")

        # Resume a previously created container without recreating it.
        wait_seconds, interval = _wait_settings(self.platform)
        deadline = time.monotonic() + wait_seconds
        while True:
            payload = _request_json(
                "GET",
                f"{base}/{container_id}",
                headers=headers,
                params={"fields": "status_code,status"},
                timeout=25,
            )
            _api_error(payload, platform="Instagram")
            status_code = str(payload.get("status_code") or "").upper()
            if status_code == "FINISHED":
                break
            if status_code in {"ERROR", "EXPIRED"}:
                raise PlatformPublishError(
                    f"Instagram media processing failed ({status_code})",
                    code=f"CONTAINER_{status_code}",
                    retryable=False,
                )
            if time.monotonic() >= deadline:
                state.update({"container_id": container_id, "stage": "container_processing"})
                on_state(state, "processing_remote")
                raise PlatformPublishError(
                    "Instagram media container is still processing; it will be polled again without creating another container",
                    code="REMOTE_PROCESSING",
                    retryable=True,
                    uncertain=True,
                )
            time.sleep(interval)

        if bool(state.get("publish_started")):
            # Instagram does not expose a portable idempotency key for
            # media_publish. Never issue it a second time after a crash/timeout
            # unless the API itself definitively reports publication.
            payload = _request_json(
                "GET",
                f"{base}/{container_id}",
                headers=headers,
                params={"fields": "status_code,status,permalink"},
                timeout=25,
            )
            _api_error(payload, platform="Instagram")
            if str(payload.get("status_code") or "").upper() in {"PUBLISHED", "PUBLISH_COMPLETE"}:
                remote_id = str(payload.get("id") or container_id)
                remote_url = str(payload.get("permalink") or "")
                on_remote_post(remote_id, remote_url)
                return PublishResult(self.platform, container_id, remote_id, remote_url)
            raise PlatformPublishError(
                "Instagram publication outcome is ambiguous; check the account manually before clearing this job. The publisher will not issue a duplicate media_publish request.",
                code="AMBIGUOUS_OUTCOME",
                retryable=False,
                uncertain=True,
            )

        state.update(
            {
                "container_id": container_id,
                "stage": "publishing",
                "publish_started": True,
                "publish_started_at": time.time(),
            }
        )
        on_state(state, "processing_remote")
        try:
            payload = _request_json(
                "POST",
                f"{base}/{user_id}/media_publish",
                headers=headers,
                data={"creation_id": container_id},
                timeout=40,
            )
            _api_error(payload, platform="Instagram")
        except PlatformPublishError as exc:
            raise PlatformPublishError(
                "Instagram media_publish may have reached the account but its response was not confirmed; manual reconciliation is required",
                code="AMBIGUOUS_OUTCOME",
                retryable=False,
                uncertain=True,
            ) from exc

        remote_id = str(payload.get("id") or "")
        if not remote_id:
            raise PlatformPublishError(
                "Instagram accepted media_publish without returning a confirmed media ID; manual reconciliation is required",
                code="AMBIGUOUS_OUTCOME",
                retryable=False,
                uncertain=True,
            )
        on_remote_post(remote_id, "")
        remote_url = ""
        try:
            details = _request_json(
                "GET",
                f"{base}/{remote_id}",
                headers=headers,
                params={"fields": "permalink"},
                timeout=15,
            )
            remote_url = str(details.get("permalink") or "")
        except PlatformPublishError:
            # The media ID is already confirmed. URL lookup must not trigger a
            # second publication attempt.
            pass
        on_remote_post(remote_id, remote_url)
        return PublishResult(self.platform, container_id, remote_id, remote_url)


class TikTokDirectPostAdapter(PlatformAdapter):
    platform = "tiktok"
    api_base = "https://open.tiktokapis.com/v2"

    def _token(self, account_id: str) -> str:
        access_token = _account_setting(
            self.platform,
            account_id,
            "access_token",
            legacy_name="TIKTOK_ACCESS_TOKEN",
        )
        refresh_token = _account_setting(self.platform, account_id, "refresh_token")
        client_key = _account_setting(self.platform, account_id, "client_key")
        client_secret = _account_setting(self.platform, account_id, "client_secret")
        try:
            return get_platform_access_token(
                platform="tiktok",
                account_id=account_id,
                access_token=access_token,
                refresh_token=refresh_token,
                client_key=client_key,
                client_secret=client_secret,
            )
        except TokenRefreshError as exc:
            raise PlatformPublishError(
                str(exc),
                code=exc.code,
                retryable=exc.retryable,
                uncertain=exc.retryable,
            ) from exc

    def _api(
        self,
        method: str,
        endpoint: str,
        *,
        token: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = _request_json(
            method,
            f"{self.api_base}/{endpoint.lstrip('/')}",
            headers={**_bearer(token), "Content-Type": "application/json; charset=UTF-8"},
            json_body=json_body,
            timeout=45,
        )
        _api_error(payload, platform="TikTok")
        if not isinstance(payload.get("data"), dict):
            raise PlatformPublishError(
                "TikTok response did not contain a data object",
                code="INVALID_API_RESPONSE",
                retryable=True,
            )
        return payload["data"]

    def _status(self, token: str, publish_id: str) -> dict[str, Any]:
        return self._api(
            "POST",
            "/post/publish/status/fetch/",
            token=token,
            json_body={"publish_id": publish_id},
        )

    @staticmethod
    def _published_result(
        publish_id: str,
        data: dict[str, Any],
        adapter_state: dict[str, Any],
        on_remote_post: RemotePostCallback,
    ) -> PublishResult:
        ids = data.get("publicaly_available_post_id") or data.get("publicly_available_post_id") or []
        post_id = str(ids[0]) if isinstance(ids, list) and ids else ""
        username = str(adapter_state.get("creator_username") or "").strip().lstrip("@")
        remote_url = (
            f"https://www.tiktok.com/@{username}/video/{post_id}"
            if post_id and username
            else ""
        )
        if post_id:
            on_remote_post(post_id, remote_url)
        return PublishResult("tiktok", publish_id, post_id or None, remote_url)

    def _finish_status(
        self,
        token: str,
        publish_id: str,
        adapter_state: dict[str, Any],
        on_state: StateCallback,
        on_remote_post: RemotePostCallback,
    ) -> PublishResult:
        wait_seconds, interval = _wait_settings(self.platform)
        deadline = time.monotonic() + wait_seconds
        while True:
            data = self._status(token, publish_id)
            status = str(data.get("status") or "").upper()
            if status == "PUBLISH_COMPLETE":
                return self._published_result(publish_id, data, adapter_state, on_remote_post)
            if status == "FAILED":
                reason = str(data.get("fail_reason") or "unspecified")[:180]
                retryable = any(
                    marker in reason.lower()
                    for marker in ("internal_error", "temporarily", "rate_limit", "network")
                )
                raise PlatformPublishError(
                    f"TikTok rejected the post ({reason})",
                    code=f"TIKTOK_{reason.upper()}",
                    retryable=retryable,
                    uncertain=False,
                )
            if time.monotonic() >= deadline:
                on_state(adapter_state, "processing_remote")
                raise PlatformPublishError(
                    f"TikTok post remains in remote state {status or 'unknown'}; polling will resume with the saved publish_id",
                    code="REMOTE_PROCESSING",
                    retryable=True,
                    uncertain=True,
                )
            time.sleep(interval)

    def publish(
        self,
        *,
        media_path: Path,
        metadata: dict[str, Any],
        account_id: str,
        idempotency_key: str,
        remote_operation_id: str | None,
        adapter_state: dict[str, Any],
        on_operation: OperationCallback,
        on_state: StateCallback,
        on_remote_post: RemotePostCallback,
    ) -> PublishResult:
        token = self._token(account_id)
        state = dict(adapter_state)
        publish_id = str(remote_operation_id or state.get("publish_id") or "")
        headers = {**_bearer(token), "Content-Type": "application/json; charset=UTF-8"}

        if publish_id:
            # The persisted publish_id is the reconciliation key. Query status
            # before any further transfer or publication request.
            status_data = self._status(token, publish_id)
            status = str(status_data.get("status") or "").upper()
            if status == "PUBLISH_COMPLETE":
                return self._published_result(publish_id, status_data, state, on_remote_post)
            if status == "FAILED":
                reason = str(status_data.get("fail_reason") or "unspecified")[:180]
                raise PlatformPublishError(
                    f"TikTok rejected the post ({reason})",
                    code=f"TIKTOK_{reason.upper()}",
                    retryable="internal_error" in reason.lower() or "rate_limit" in reason.lower(),
                )
            upload_url = str(state.get("upload_url") or "")
            if state.get("stage") == "uploading" and upload_url:
                self._upload_remaining(
                    media_path,
                    upload_url,
                    token,
                    int(status_data.get("uploaded_bytes", state.get("uploaded_bytes", 0)) or 0),
                    state,
                    on_state,
                )
                state["stage"] = "submitted"
                on_state(state, "processing_remote")
            return self._finish_status(token, publish_id, state, on_state, on_remote_post)

        if media_path.suffix.lower() not in {".mp4", ".mov", ".webm"}:
            raise PlatformPublishError(
                "TikTok Direct Post requires an MP4, MOV, or WebM file",
                code="UNSUPPORTED_MEDIA",
                retryable=False,
            )
        file_size = media_path.stat().st_size
        chunk_size, chunk_count = _tiktok_chunk_plan(file_size)

        creator = self._api(
            "POST",
            "/post/publish/creator_info/query/",
            token=token,
            json_body={},
        )
        target_metadata = metadata.get("_platform_override")
        overrides = target_metadata if isinstance(target_metadata, dict) else {}
        privacy = str(overrides.get("privacy_level") or "").strip().upper()
        options = creator.get("privacy_level_options")
        if not privacy:
            raise PlatformPublishError(
                "TikTok privacy_level must be selected explicitly in the platform override before publishing",
                code="PRIVACY_SELECTION_REQUIRED",
                retryable=False,
            )
        if not isinstance(options, list) or privacy not in {str(value).upper() for value in options}:
            raise PlatformPublishError(
                "TikTok privacy_level must match the current creator_info/query response",
                code="PRIVACY_LEVEL_MISMATCH",
                retryable=False,
            )
        if privacy == "PUBLIC_TO_EVERYONE" and not _truthy("AIVF_TIKTOK_ALLOW_PUBLIC"):
            raise PlatformPublishError(
                "public TikTok Direct Post is blocked until AIVF_TIKTOK_ALLOW_PUBLIC=1 explicitly opts in",
                code="PUBLIC_PUBLISH_NOT_ACKNOWLEDGED",
                retryable=False,
            )
        probe = metadata.get("_media_probe")
        if isinstance(probe, dict):
            try:
                duration = float(probe.get("duration_seconds") or 0)
                max_duration = float(creator.get("max_video_post_duration_sec") or 0)
            except (ValueError, TypeError):
                duration, max_duration = 0, 0
            if duration > 0 and max_duration > 0 and duration > max_duration:
                raise PlatformPublishError(
                    f"video duration exceeds the current TikTok account limit of {int(max_duration)} seconds",
                    code="MEDIA_DURATION_LIMIT",
                    retryable=False,
                )

        caption = str(overrides.get("caption") or metadata.get("title") or media_path.stem)
        description = str(metadata.get("description") or "").strip()
        if description and description not in caption:
            caption = f"{caption}\n\n{description}"
        if len(caption.encode("utf-16-le")) // 2 > 2200:
            raise PlatformPublishError(
                "TikTok caption exceeds 2200 UTF-16 code units",
                code="INVALID_METADATA",
                retryable=False,
            )
        post_info: dict[str, Any] = {
            "title": caption,
            "privacy_level": privacy,
            "disable_duet": bool(overrides.get("disable_duet", False)),
            "disable_comment": bool(overrides.get("disable_comment", False)),
            "disable_stitch": bool(overrides.get("disable_stitch", False)),
            "is_aigc": metadata.get("ai_generated") is True,
        }
        for optional_field in ("brand_content_toggle", "brand_organic_toggle"):
            if optional_field in overrides:
                post_info[optional_field] = bool(overrides[optional_field])
        init = self._api(
            "POST",
            "/post/publish/video/init/",
            token=token,
            json_body={
                "post_info": post_info,
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": file_size,
                    "chunk_size": chunk_size,
                    "total_chunk_count": chunk_count,
                },
            },
        )
        publish_id = str(init.get("publish_id") or "")
        upload_url = str(init.get("upload_url") or "")
        if not publish_id or not upload_url:
            raise PlatformPublishError(
                "TikTok did not return both publish_id and upload_url",
                code="INVALID_API_RESPONSE",
                retryable=True,
                uncertain=True,
            )
        parsed_upload = urlparse(upload_url)
        if parsed_upload.scheme != "https" or not (parsed_upload.hostname or "").lower().endswith(".tiktokapis.com"):
            raise PlatformPublishError(
                "TikTok returned an upload URL outside the expected HTTPS API domain",
                code="UNTRUSTED_UPLOAD_URL",
                retryable=False,
                uncertain=True,
            )
        state.update(
            {
                "publish_id": publish_id,
                "upload_url": upload_url,
                "stage": "uploading",
                "uploaded_bytes": 0,
                "creator_username": str(creator.get("creator_username") or ""),
                "privacy_level": privacy,
            }
        )
        on_operation(publish_id, state, "uploading")
        self._upload_remaining(media_path, upload_url, token, 0, state, on_state)
        state["stage"] = "submitted"
        on_state(state, "processing_remote")
        return self._finish_status(token, publish_id, state, on_state, on_remote_post)

    @staticmethod
    def _upload_remaining(
        media_path: Path,
        upload_url: str,
        token: str,
        uploaded_bytes: int,
        state: dict[str, Any],
        on_state: StateCallback,
    ) -> None:
        size = media_path.stat().st_size
        if uploaded_bytes < 0 or uploaded_bytes > size:
            raise PlatformPublishError(
                "TikTok upload progress is outside the source file bounds",
                code="INVALID_UPLOAD_PROGRESS",
                retryable=False,
                uncertain=True,
            )
        chunk_size, _chunk_count = _tiktok_chunk_plan(size)
        mime = mimetypes.guess_type(media_path.name)[0]
        mime = mime if mime in {"video/mp4", "video/quicktime", "video/webm"} else {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
        }.get(media_path.suffix.lower(), "application/octet-stream")
        with media_path.open("rb") as handle:
            offset = uploaded_bytes
            handle.seek(offset)
            while offset < size:
                data = handle.read(min(chunk_size, size - offset))
                if not data:
                    raise PlatformPublishError(
                        "local video ended before the declared upload size",
                        code="LOCAL_FILE_CHANGED",
                        retryable=False,
                        uncertain=True,
                    )
                end = offset + len(data) - 1
                try:
                    response = requests.put(
                        upload_url,
                        headers={
                            "Content-Type": mime,
                            "Content-Length": str(len(data)),
                            "Content-Range": f"bytes {offset}-{end}/{size}",
                        },
                        data=data,
                        timeout=180,
                        allow_redirects=False,
                    )
                except requests.RequestException as exc:
                    raise PlatformPublishError(
                        f"TikTok media transfer failed ({type(exc).__name__}); resume will query the saved publish_id first",
                        code="UPLOAD_TRANSFER_FAILED",
                        retryable=True,
                        uncertain=True,
                    ) from exc
                if response.status_code not in {200, 201, 206, 308}:
                    retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
                    raise PlatformPublishError(
                        f"TikTok media transfer returned HTTP {response.status_code}",
                        code=f"UPLOAD_HTTP_{response.status_code}",
                        retryable=retryable,
                        uncertain=True,
                    )
                offset += len(data)
                state["uploaded_bytes"] = offset
                on_state(dict(state), "uploading")


def adapter_for(platform: str) -> PlatformAdapter:
    normalized = platform.strip().lower()
    if normalized == "youtube":
        return YouTubeAdapter()
    if normalized == "instagram":
        return InstagramReelsAdapter()
    if normalized == "tiktok":
        return TikTokDirectPostAdapter()
    raise PlatformPublishError(
        f"unsupported publishing platform {normalized!r}",
        code="UNSUPPORTED_PLATFORM",
        retryable=False,
    )


__all__ = [
    "PlatformAdapter",
    "PlatformPublishError",
    "PublishResult",
    "YouTubeAdapter",
    "InstagramReelsAdapter",
    "TikTokDirectPostAdapter",
    "adapter_for",
]
