"""Instagram Reels publishing through the Meta Graph API.

Instagram's API imports media from a public HTTPS URL. This integration deliberately
requires a pre-hosted, public URL and an Instagram professional account; it does not
make local files public or upload local bytes to Instagram. Ambiguous publish requests
are reconciled against recent media or failed closed to avoid duplicate publishing.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests

DEFAULT_GRAPH_BASE = "https://graph.facebook.com"
DEFAULT_POLL_TIMEOUT_SECONDS = 15 * 60
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
MAX_CAPTION_CHARS = 2200
MAX_METADATA_AGE_SECONDS = 24 * 60 * 60


def _graph_version() -> str:
    version = os.environ.get("AIVF_INSTAGRAM_GRAPH_API_VERSION", "").strip()
    if not version or not version.startswith("v") or not version[1:].replace(".", "").isdigit():
        raise RuntimeError(
            "Set AIVF_INSTAGRAM_GRAPH_API_VERSION to a supported Meta Graph API version, e.g. vXX.0"
        )
    return version


def _graph_base() -> str:
    base = os.environ.get("AIVF_INSTAGRAM_GRAPH_BASE", DEFAULT_GRAPH_BASE).strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("AIVF_INSTAGRAM_GRAPH_BASE must be an HTTPS URL without embedded credentials")
    return f"{base}/{_graph_version()}"


def _user_id() -> str:
    value = os.environ.get("AIVF_INSTAGRAM_USER_ID", "").strip()
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for ch in value):
        raise RuntimeError("Set AIVF_INSTAGRAM_USER_ID to the authorized Instagram professional account ID")
    return value


def _access_token() -> str:
    path_value = os.environ.get("AIVF_INSTAGRAM_TOKEN_PATH", "./state/instagram-token.json")
    token_path = Path(path_value).expanduser()
    if token_path.is_file():
        try:
            payload = json.loads(token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Instagram token file is unreadable; re-authorize before publishing") from exc
        token = str(payload.get("access_token") or "").strip()
        if not token:
            raise RuntimeError("Instagram token file has no access_token")
        expires_at = payload.get("expires_at")
        if expires_at is not None:
            try:
                expired = float(expires_at) <= time.time() + 300
            except (TypeError, ValueError):
                raise RuntimeError("Instagram token expiry is invalid; re-authorize before publishing")
            if expired:
                raise RuntimeError("Instagram access token is expired/expiring; refresh or re-authorize before publishing")
        return token
    token = os.environ.get("AIVF_INSTAGRAM_ACCESS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Instagram is not configured: set AIVF_INSTAGRAM_ACCESS_TOKEN or AIVF_INSTAGRAM_TOKEN_PATH"
        )
    return token


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    os.replace(temporary, path)


def _journal_path(idempotency_key: str, directory: Optional[str]) -> Path:
    root = Path(
        directory
        or os.environ.get("AIVF_INSTAGRAM_IDEMPOTENCY_DIR", "./state/instagram-idempotency")
    ).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root / (hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest() + ".json")


def _request(
    method: str,
    url: str,
    *,
    token: str,
    params: Optional[dict[str, Any]] = None,
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    try:
        if method.upper() == "GET":
            response = requests.request(
                method,
                url,
                params={**(params or {}), "access_token": token},
                timeout=(10, 45),
            )
        else:
            response = requests.request(
                method,
                url,
                params=params,
                data={**(data or {}), "access_token": token},
                timeout=(10, 45),
            )
    except requests.RequestException as exc:
        raise RuntimeError("Instagram API request failed before its result could be verified") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Instagram API returned non-JSON content (HTTP {response.status_code})") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Instagram API returned an invalid response shape")
    error = payload.get("error")
    if not response.ok or error:
        details = error if isinstance(error, dict) else {}
        message = str(details.get("message") or "request rejected").replace(token, "[REDACTED]")[:500]
        code = details.get("code")
        raise RuntimeError(
            f"Instagram API rejected the request (HTTP {response.status_code}, code={code}): {message}"
        )
    return payload


def _container_status(token: str, container_id: str, base: str) -> dict[str, Any]:
    payload = _request(
        "GET",
        f"{base}/{container_id}",
        token=token,
        params={"fields": "status_code,status"},
    )
    status_code = str(payload.get("status_code") or "").upper()
    if not status_code:
        raise RuntimeError("Instagram returned no media container status")
    return {"status_code": status_code, "status": str(payload.get("status") or "")[:500]}


def _recent_matching_media(
    token: str,
    *,
    user_id: str,
    base: str,
    caption: str,
    created_after: float,
) -> list[dict[str, Any]]:
    url = f"{base}/{user_id}/media"
    params: dict[str, Any] = {"fields": "id,caption,timestamp,permalink,media_type,media_product_type", "limit": 25}
    found: list[dict[str, Any]] = []
    pages = 0
    while url and pages < 4:
        payload = _request("GET", url, token=token, params=params)
        params = {}
        for item in payload.get("data") or []:
            if not isinstance(item, dict) or str(item.get("caption") or "") != caption:
                continue
            stamp = str(item.get("timestamp") or "")
            try:
                when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                epoch = when.astimezone(timezone.utc).timestamp()
            except (ValueError, TypeError, OverflowError):
                continue
            if epoch >= created_after and str(item.get("media_product_type") or "").upper() in {"REELS", "FEED"}:
                found.append(item)
        next_url = ((payload.get("paging") or {}).get("next") or "")
        # Graph API paging URLs are server-provided. Require HTTPS and Meta host to avoid leaking tokens.
        parsed = urlparse(next_url)
        if next_url and (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith("facebook.com")):
            raise RuntimeError("Instagram returned an unexpected media paging URL; refusing remote reconciliation")
        url = next_url
        pages += 1
    if len(found) > 1:
        raise RuntimeError("Multiple recent Instagram posts match the uncertain caption; manual reconciliation is required")
    return found


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_video(
    video_path: str,
    *,
    title: str,
    description: str,
    tags: Optional[list[str]] = None,
    privacy_status: str = "public",
    video_url: str,
    share_to_feed: bool = True,
    idempotency_key: str,
    idempotency_directory: Optional[str] = None,
    max_poll_seconds: Optional[int] = None,
    poll_interval_seconds: Optional[float] = None,
    **_: Any,
) -> dict[str, Any]:
    """Publish one Reels container using a publicly accessible HTTPS video URL."""
    path = Path(video_path)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    if str(privacy_status).lower() != "public":
        raise ValueError("Instagram Graph API Reels publishing is public; choose a supported visibility policy")
    parsed = urlparse(str(video_url).strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Instagram video_url must be a publicly accessible HTTPS URL")
    if not isinstance(share_to_feed, bool):
        raise ValueError("share_to_feed must be a boolean")
    parts = [str(title or "").strip(), str(description or "").strip()]
    cleaned_tags = [str(tag).strip().lstrip("#").replace(" ", "") for tag in (tags or []) if str(tag).strip()]
    if cleaned_tags:
        parts.append(" ".join(f"#{tag}" for tag in cleaned_tags))
    caption = "\n\n".join(part for part in parts if part)
    if not caption or len(caption) > MAX_CAPTION_CHARS:
        raise ValueError(f"Instagram Reels caption must be between 1 and {MAX_CAPTION_CHARS} characters")

    base = _graph_base()
    user_id = _user_id()
    token = _access_token()
    journal = _journal_path(idempotency_key, idempotency_directory)
    source_sha256 = _file_sha256(path)
    if journal.exists():
        try:
            record = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Instagram idempotency journal is unreadable; refusing a duplicate post") from exc
        if not isinstance(record, dict):
            raise RuntimeError("Instagram idempotency journal is invalid; refusing a duplicate post")
        if record.get("state") == "published":
            return dict(record.get("result") or {})
        if record.get("source_sha256") != source_sha256 or record.get("caption") != caption:
            raise RuntimeError("Instagram source or caption changed after initialization; manual review is required")
        if record.get("state") == "publish_started":
            # The request may have succeeded remotely even when its response was lost.
            matches = _recent_matching_media(
                token,
                user_id=user_id,
                base=base,
                caption=caption,
                created_after=float(record.get("attempt_started_at") or 0),
            )
            if len(matches) == 1:
                item = matches[0]
                result = {
                    "video_id": str(item.get("id") or ""),
                    "url": item.get("permalink"),
                    "status": "PUBLISHED",
                    "deduplicated": True,
                }
                record.update(state="published", result=result, completed_at=time.time())
                _write_json(journal, record)
                return result
            raise RuntimeError(
                "Instagram publish outcome is uncertain and no unique matching remote post was found; "
                "refusing to publish again until the account is reconciled manually"
            )
    else:
        record = {}

    if not record:
        attempt_started = time.time()
        record = {
            "state": "container_create_started",
            "source_sha256": source_sha256,
            "video_url_hash": hashlib.sha256(str(video_url).encode("utf-8")).hexdigest(),
            "caption": caption,
            "attempt_started_at": attempt_started,
            "created_at": attempt_started,
        }
        _write_json(journal, record)
        # The URL has to be reachable by Meta from the public internet. We do not fetch
        # it here because many signed URLs are short-lived and source hosting is external.
        try:
            created = _request(
                "POST",
                f"{base}/{user_id}/media",
                token=token,
                data={
                    "media_type": "REELS",
                    "video_url": str(video_url),
                    "caption": caption,
                    "share_to_feed": str(share_to_feed).lower(),
                },
            )
        except RuntimeError as exc:
            if "before its result could be verified" in str(exc):
                raise RuntimeError(
                    "Instagram container creation outcome is uncertain; refusing to create another container automatically"
                ) from exc
            raise
        container_id = str(created.get("id") or "").strip()
        if not container_id:
            raise RuntimeError("Instagram did not return a media container ID")
        record.update(state="container_created", container_id=container_id, share_to_feed=share_to_feed)
        _write_json(journal, record)

    container_id = str(record.get("container_id") or "").strip()
    if not container_id:
        if record.get("state") == "container_create_started":
            raise RuntimeError("Instagram container creation outcome is uncertain; manual reconciliation is required")
        raise RuntimeError("Instagram idempotency journal has no container ID; refusing duplicate container creation")

    timeout = max_poll_seconds if max_poll_seconds is not None else int(
        os.environ.get("AIVF_INSTAGRAM_POLL_TIMEOUT_SECONDS", DEFAULT_POLL_TIMEOUT_SECONDS)
    )
    interval = poll_interval_seconds if poll_interval_seconds is not None else float(
        os.environ.get("AIVF_INSTAGRAM_POLL_INTERVAL_SECONDS", str(DEFAULT_POLL_INTERVAL_SECONDS))
    )
    deadline = time.monotonic() + max(0, timeout)
    while True:
        try:
            status = _container_status(token, container_id, base)
        except RuntimeError as exc:
            raise RuntimeError(f"Instagram container status could not be verified: {exc}") from exc
        if status["status_code"] == "FINISHED":
            record.update(state="container_ready", container_status=status["status_code"])
            _write_json(journal, record)
            break
        if status["status_code"] in {"ERROR", "EXPIRED"}:
            record.update(state="failed", failure=status)
            _write_json(journal, record)
            raise RuntimeError(f"Instagram rejected or expired the media container: {status['status']}")
        if time.monotonic() >= deadline:
            record.update(state="container_processing", container_status=status["status_code"])
            _write_json(journal, record)
            raise RuntimeError("Instagram container is still processing; the saved container will be resumed")
        time.sleep(max(0.0, min(interval, 30.0)))

    if record.get("state") == "publish_started":
        raise RuntimeError(
            "Instagram publish outcome is uncertain; refusing a repeat publish request without reconciliation"
        )
    record.update(state="publish_started", publish_started_at=time.time())
    _write_json(journal, record)
    try:
        published = _request(
            "POST",
            f"{base}/{user_id}/media_publish",
            token=token,
            data={"creation_id": container_id},
        )
    except RuntimeError as exc:
        if "before its result could be verified" in str(exc):
            raise RuntimeError(
                "Instagram publish outcome is uncertain; the saved journal blocks an automatic duplicate"
            ) from exc
        raise
    media_id = str(published.get("id") or "").strip()
    if not media_id:
        raise RuntimeError(
            "Instagram publish response returned no media ID; journal retained for safe manual reconciliation"
        )
    result = {
        "video_id": media_id,
        "url": None,
        "status": "PUBLISHED",
        "container_id": container_id,
        "deduplicated": False,
    }
    record.update(state="published", result=result, completed_at=time.time())
    _write_json(journal, record)

    # Populate permalink if available; failure to fetch it does not undo the published state.
    try:
        item = _request("GET", f"{base}/{media_id}", token=token, params={"fields": "id,permalink,media_type"})
        permalink = item.get("permalink")
        if permalink:
            result["url"] = str(permalink)
            record["result"] = result
            _write_json(journal, record)
    except RuntimeError:
        pass
    return result
