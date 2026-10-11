"""TikTok Content Posting API direct-video publisher with resumable local state.

Requires an approved TikTok app with video.publish and a user-authorized token.
The access/upload credentials are never written to logs. Ambiguous init/publish
responses are reconciled or failed closed instead of blindly starting a new post.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import time
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

API_BASE = "https://open.tiktokapis.com/v2"
INIT_URL = f"{API_BASE}/post/publish/video/init/"
CREATOR_INFO_URL = f"{API_BASE}/post/publish/creator_info/query/"
STATUS_URL = f"{API_BASE}/post/publish/status/fetch/"
TOKEN_URL = f"{API_BASE}/oauth/token/"
DEFAULT_CHUNK_SIZE = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 4 * 1024 * 1024 * 1024
MAX_POLL_SECONDS = 15 * 60


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    os.replace(temporary, path)


def _journal_path(idempotency_key: str, directory: Optional[str]) -> Path:
    root = Path(
        directory
        or os.environ.get("AIVF_TIKTOK_IDEMPOTENCY_DIR", "./state/tiktok-idempotency")
    ).expanduser()
    safe_key = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe_key}.json"


def _load_token() -> tuple[str, dict[str, Any]]:
    token_path = Path(
        os.environ.get("AIVF_TIKTOK_TOKEN_PATH", "./state/tiktok-token.json")
    ).expanduser()
    if token_path.is_file():
        try:
            record = json.loads(token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("TikTok token file is unreadable; re-authorize before publishing") from exc
        access = str(record.get("access_token") or "").strip()
        expires_at = float(record.get("expires_at") or 0)
        if not access:
            raise RuntimeError("TikTok token file has no access_token; re-authorize before publishing")
        if expires_at and expires_at <= time.time() + 120:
            refresh_expiry = float(record.get("refresh_expires_at") or 0)
            if refresh_expiry and refresh_expiry <= time.time() + 120:
                raise RuntimeError("TikTok refresh token is expired; run aivf-tiktok-authorize again")
            refresh_token = str(record.get("refresh_token") or "").strip()
            client_key = os.environ.get("AIVF_TIKTOK_CLIENT_KEY", "").strip()
            client_secret = os.environ.get("AIVF_TIKTOK_CLIENT_SECRET", "").strip()
            if not refresh_token or not client_key or not client_secret:
                raise RuntimeError("TikTok token is expiring/expired; configure refresh credentials or re-authorize")
            try:
                response = requests.post(
                    TOKEN_URL,
                    data={
                        "client_key": client_key,
                        "client_secret": client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    timeout=(10, 30),
                )
                payload = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise RuntimeError("TikTok token refresh failed without a verified response") from exc
            data = payload if isinstance(payload, dict) else {}
            if not response.ok or data.get("error"):
                raise RuntimeError("TikTok token refresh was rejected; re-authorize before publishing")
            access = str(data.get("access_token") or "").strip()
            if not access:
                raise RuntimeError("TikTok token refresh returned no access token")
            record["access_token"] = access
            if data.get("refresh_token"):
                record["refresh_token"] = str(data["refresh_token"])
            if data.get("expires_in"):
                record["expires_at"] = time.time() + float(data["expires_in"])
            if data.get("refresh_expires_in"):
                record["refresh_expires_at"] = time.time() + float(data["refresh_expires_in"])
            _write_json(token_path, record)
        return access, record

    access = os.environ.get("AIVF_TIKTOK_ACCESS_TOKEN", "").strip()
    if not access:
        raise RuntimeError(
            "TikTok is not configured: provide AIVF_TIKTOK_TOKEN_PATH with an authorized token "
            "or set AIVF_TIKTOK_ACCESS_TOKEN"
        )
    return access, {"access_token": access, "open_id": os.environ.get("AIVF_TIKTOK_OPEN_ID", "")}


def _api_json(method: str, url: str, *, token: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
            json=body,
            timeout=(10, 45),
        )
    except requests.RequestException as exc:
        raise RuntimeError("TikTok API request failed before a response could be verified") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"TikTok API returned a non-JSON response (HTTP {response.status_code})") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("TikTok API returned an invalid response shape")
    error = payload.get("error") or {}
    code = str(error.get("code") or "").strip().lower()
    if not response.ok or (code and code != "ok"):
        message = str(error.get("message") or "request rejected").replace(token, "[REDACTED]")[:500]
        raise RuntimeError(f"TikTok API rejected the request (HTTP {response.status_code}, {code or 'http_error'}): {message}")
    return payload


def _video_status(token: str, publish_id: str) -> dict[str, Any]:
    payload = _api_json("POST", STATUS_URL, token=token, body={"publish_id": publish_id})
    data = payload.get("data") or {}
    if not isinstance(data, dict) or not data.get("status"):
        raise RuntimeError("TikTok status API returned no publish status")
    return data


def _privacy_level(privacy_status: str) -> str:
    mapping = {"private": "SELF_ONLY", "public": "PUBLIC_TO_EVERYONE"}
    value = mapping.get(str(privacy_status).strip().lower())
    if value is None:
        raise ValueError("TikTok supports private or public here; it has no unlisted visibility equivalent")
    return value


def _upload_chunks(path: Path, *, token: str, record: dict[str, Any], journal_path: Path) -> None:
    upload_url = str(record.get("upload_url") or "").strip()
    if not upload_url.startswith("https://"):
        raise RuntimeError("TikTok upload session has no valid HTTPS upload URL; manual review is required")
    total = path.stat().st_size
    chunk_size = int(record.get("chunk_size") or min(DEFAULT_CHUNK_SIZE, total))
    offset = int(record.get("uploaded_bytes") or 0)
    if offset < 0 or offset > total:
        raise RuntimeError("TikTok upload journal has an invalid byte offset; refusing to restart upload")
    with path.open("rb") as video:
        video.seek(offset)
        while offset < total:
            chunk = video.read(min(chunk_size, total - offset))
            if not chunk:
                raise RuntimeError("TikTok upload source ended unexpectedly")
            end = offset + len(chunk) - 1
            try:
                response = requests.put(
                    upload_url,
                    data=chunk,
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{total}",
                    },
                    timeout=(15, 180),
                )
            except requests.RequestException as exc:
                # Keep upload_url and offset: the next retry resumes the same remote session.
                raise RuntimeError("TikTok media transfer interrupted; the saved session can be resumed") from exc
            if response.status_code not in {200, 201, 206, 204}:
                raise RuntimeError(
                    f"TikTok media transfer failed with HTTP {response.status_code}; remote session retained"
                )
            offset += len(chunk)
            record["uploaded_bytes"] = offset
            record["state"] = "processing"
            _write_json(journal_path, record)


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
    privacy_status: str = "private",
    idempotency_key: str,
    idempotency_directory: Optional[str] = None,
    max_poll_seconds: Optional[int] = None,
    poll_interval_seconds: Optional[float] = None,
    **_: Any,
) -> dict[str, Any]:
    """Direct-post one MP4 and resume from the stored TikTok upload session."""
    path = Path(video_path)
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size <= 0 or size > MAX_VIDEO_BYTES:
        raise ValueError(f"TikTok video size must be between 1 byte and {MAX_VIDEO_BYTES} bytes")
    privacy = _privacy_level(privacy_status)
    caption_parts = [str(title).strip(), str(description or "").strip()]
    normalized_tags = [
        str(tag).strip().lstrip("#").replace(" ", "")
        for tag in (tags or [])
        if str(tag).strip()
    ]
    if normalized_tags:
        caption_parts.append(" ".join(f"#{tag}" for tag in normalized_tags))
    caption = "\n\n".join(part for part in caption_parts if part)
    if not caption or len(caption) > 2200:
        raise ValueError("TikTok caption must be between 1 and 2200 characters")

    token, token_record = _load_token()
    journal_path = _journal_path(idempotency_key, idempotency_directory)
    if journal_path.exists():
        try:
            record = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("TikTok idempotency journal is unreadable; refusing a duplicate post") from exc
        if not isinstance(record, dict):
            raise RuntimeError("TikTok idempotency journal is invalid; refusing a duplicate post")
        if record.get("state") == "published":
            return dict(record.get("result") or {})
        if record.get("source_size") != size or (
            record.get("source_sha256") and record.get("source_sha256") != _file_sha256(path)
        ):
            raise RuntimeError("TikTok source changed after initialization; refusing to reuse the upload session")
        if record.get("privacy_level") != privacy:
            raise RuntimeError("TikTok destination visibility changed after initialization; review this job manually")
        if record.get("state") == "failed":
            record = {}
    else:
        record = {}

    if not record:
        # Commit an intent record before the remote init call. If the process dies
        # after TikTok creates a post but before returning its publish_id, the next
        # run sees init_started and fails closed rather than initializing another post.
        record = {
            "state": "init_started",
            "source_size": size,
            "source_sha256": _file_sha256(path),
            "privacy_level": privacy,
            "attempt_started_at": time.time(),
            "created_at": time.time(),
        }
        _write_json(journal_path, record)
        try:
            creator_payload = _api_json("POST", CREATOR_INFO_URL, token=token, body={})
        except RuntimeError as exc:
            raise RuntimeError(f"TikTok creator authorization check failed: {exc}") from exc
        creator = creator_payload.get("data") or {}
        options = creator.get("privacy_level_options") or []
        if privacy not in options:
            raise RuntimeError(f"TikTok account does not permit requested privacy level {privacy}")
        max_duration = float(creator.get("max_video_post_duration_sec") or 0)
        try:
            import subprocess
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                check=True, capture_output=True, text=True, timeout=20,
            )
            duration = float((probe.stdout or "0").strip() or 0)
        except Exception as exc:
            raise RuntimeError("TikTok preflight could not verify video duration") from exc
        if max_duration > 0 and duration > max_duration:
            raise ValueError(f"TikTok account accepts at most {max_duration:g} seconds; this video is {duration:g} seconds")
        chunk_size = min(DEFAULT_CHUNK_SIZE, size)
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk_size,
            "total_chunk_count": int(math.ceil(size / chunk_size)),
        }
        body = {
            "post_info": {
                "title": caption,
                "privacy_level": privacy,
                "disable_comment": bool(creator.get("comment_disabled", False)),
                "disable_duet": bool(creator.get("duet_disabled", False)),
                "disable_stitch": bool(creator.get("stitch_disabled", False)),
                "video_cover_timestamp_ms": 1000,
                "is_aigc": bool(_.get("ai_generated", False) or _.get("realistic_alteration", False)),
            },
            "source_info": source_info,
        }
        try:
            payload = _api_json("POST", INIT_URL, token=token, body=body)
        except RuntimeError as exc:
            if "before a response could be verified" in str(exc):
                raise RuntimeError("TikTok initialization outcome is uncertain; inspect the account before retrying") from exc
            raise
        data = payload.get("data") or {}
        publish_id = str(data.get("publish_id") or "").strip()
        upload_url = str(data.get("upload_url") or "").strip()
        if not publish_id or not upload_url.startswith("https://"):
            raise RuntimeError("TikTok initialization returned no verifiable upload session; inspect the account before retrying")
        record = {
            "state": "uploading",
            "publish_id": publish_id,
            "upload_url": upload_url,
            "uploaded_bytes": 0,
            "chunk_size": chunk_size,
            "source_size": size,
            "privacy_level": privacy,
            "attempt_started_at": float(record.get("attempt_started_at") or time.time()),
            "created_at": float(record.get("created_at") or time.time()),
            "source_sha256": _file_sha256(path),
        }
        _write_json(journal_path, record)

    publish_id = str(record.get("publish_id") or "").strip()
    if not publish_id:
        raise RuntimeError("TikTok journal has no publish_id; refusing to initialize another post")
    state = str(record.get("state") or "")
    if state in {"uploading", "processing"} and int(record.get("uploaded_bytes") or 0) < size:
        _upload_chunks(path, token=token, record=record, journal_path=journal_path)
    if record.get("state") != "published":
        record["state"] = "processing"
        record["upload_url"] = ""  # Do not retain a signed upload URL once the media transfer is complete.
        _write_json(journal_path, record)

    timeout = max_poll_seconds if max_poll_seconds is not None else int(os.environ.get("AIVF_TIKTOK_POLL_TIMEOUT_SECONDS", MAX_POLL_SECONDS))
    interval = poll_interval_seconds if poll_interval_seconds is not None else float(os.environ.get("AIVF_TIKTOK_POLL_INTERVAL_SECONDS", "5"))
    deadline = time.monotonic() + max(0, timeout)
    while True:
        status_data = _video_status(token, publish_id)
        status = str(status_data.get("status") or "").upper()
        if status == "PUBLISH_COMPLETE":
            raw_post_ids = (
                status_data.get("publicaly_available_post_id")
                or status_data.get("publicly_available_post_id")
                or []
            )
            if isinstance(raw_post_ids, str):
                published_ids = [raw_post_ids] if raw_post_ids else []
            elif isinstance(raw_post_ids, list):
                published_ids = [str(value) for value in raw_post_ids if str(value).strip()]
            else:
                published_ids = []
            post_id = published_ids[0] if published_ids else publish_id
            result = {
                "video_id": post_id,
                "publish_id": publish_id,
                "url": f"https://www.tiktok.com/@{str(token_record.get('open_id') or os.environ.get('AIVF_TIKTOK_OPEN_ID', 'user'))}/video/{post_id}" if published_ids else None,
                "status": status,
                "privacy_level": privacy,
                "publicly_available_post_ids": published_ids,
                "deduplicated": False,
            }
            record["state"] = "published"
            record["result"] = result
            record["completed_at"] = time.time()
            record.pop("upload_url", None)
            _write_json(journal_path, record)
            return result
        if status == "FAILED":
            record["state"] = "failed"
            record["failure_reason"] = str(status_data.get("fail_reason") or "TikTok reports the post failed")[:500]
            record["failed_at"] = time.time()
            record.pop("upload_url", None)
            _write_json(journal_path, record)
            raise RuntimeError(f"TikTok confirmed the post failed: {record['failure_reason']}")
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "TikTok publish is still processing; the saved publish_id will be reconciled on the next retry"
            )
        time.sleep(max(0.0, min(interval, 30.0)))


def authorize_main(argv: Optional[list[str]] = None) -> int:
    """Complete one interactive TikTok OAuth authorization and save the refresh token."""
    parser = argparse.ArgumentParser(description="Authorize Edit Factory to publish TikTok videos")
    parser.add_argument("--timeout", type=int, default=300, help="OAuth callback timeout in seconds")
    args = parser.parse_args(argv)
    if args.timeout < 30 or args.timeout > 900:
        raise SystemExit("--timeout must be between 30 and 900 seconds")

    client_key = os.environ.get("AIVF_TIKTOK_CLIENT_KEY", "").strip()
    client_secret = os.environ.get("AIVF_TIKTOK_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("AIVF_TIKTOK_REDIRECT_URI", "").strip()
    if not client_key or not client_secret:
        raise SystemExit("Set AIVF_TIKTOK_CLIENT_KEY and AIVF_TIKTOK_CLIENT_SECRET first.")
    if not redirect_uri:
        raise SystemExit(
            "Set AIVF_TIKTOK_REDIRECT_URI to the exact callback URL registered for your TikTok app."
        )
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit(
            "For this local authorization helper, AIVF_TIKTOK_REDIRECT_URI must be an HTTP loopback URL "
            "such as http://127.0.0.1:8765/callback and must exactly match the app registration."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("The TikTok redirect URI cannot contain credentials, query parameters, or a fragment.")
    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise SystemExit("AIVF_TIKTOK_REDIRECT_URI contains an invalid port.") from exc
    callback_path = parsed.path or "/"
    state = secrets.token_urlsafe(32)

    class CallbackServer(HTTPServer):
        auth_state = state
        callback_code: Optional[str] = None
        callback_error: Optional[str] = None
        completed = False

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            # Never log OAuth callback query strings or authorization codes.
            return

        def do_GET(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path != callback_path:
                self.send_error(404)
                return
            if (query.get("state") or [""])[0] != self.server.auth_state:
                self.server.callback_error = "OAuth state verification failed."
            elif (query.get("error") or [""])[0]:
                self.server.callback_error = "TikTok authorization was denied or failed."
            else:
                code = (query.get("code") or [""])[0].strip()
                if not code:
                    self.server.callback_error = "TikTok returned no authorization code."
                else:
                    self.server.callback_code = code
            self.server.completed = True
            message = (
                b"Authorization received. You may close this browser tab."
                if self.server.callback_code
                else b"Authorization failed. Return to PowerShell and review the error."
            )
            self.send_response(200 if self.server.callback_code else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(message)

    server = CallbackServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = 1.0
    authorize_url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(
        {
            "client_key": client_key,
            "response_type": "code",
            "scope": "user.info.basic,video.publish",
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    print("Opening TikTok authorization in your browser. Do not share the resulting token file.")
    try:
        webbrowser.open(authorize_url)
        deadline = time.monotonic() + args.timeout
        while not server.completed and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not server.completed:
        raise SystemExit("TikTok OAuth timed out without a callback.")
    if server.callback_error or not server.callback_code:
        raise SystemExit(server.callback_error or "TikTok OAuth failed.")

    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": server.callback_code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=(10, 30),
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SystemExit("TikTok token exchange failed; authorize again after checking app configuration.") from exc
    if not response.ok or not isinstance(payload, dict) or payload.get("error"):
        details = payload.get("error_description") or payload.get("error") if isinstance(payload, dict) else "invalid response"
        raise SystemExit(f"TikTok token exchange was rejected: {str(details)[:300]}")
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    open_id = str(payload.get("open_id") or "").strip()
    if not access_token or not refresh_token or not open_id:
        raise SystemExit("TikTok token exchange did not return access_token, refresh_token, and open_id.")
    now = time.time()
    token_record: dict[str, Any] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "open_id": open_id,
        "scope": str(payload.get("scope") or ""),
        "token_type": str(payload.get("token_type") or "Bearer"),
        "created_at": now,
        "expires_at": now + float(payload.get("expires_in") or 0),
        "refresh_expires_at": now + float(payload.get("refresh_expires_in") or 0),
    }
    token_path = Path(os.environ.get("AIVF_TIKTOK_TOKEN_PATH", "./state/tiktok-token.json")).expanduser()
    _write_json(token_path, token_record)
    print(f"TikTok authorization saved to {token_path}. Keep the file private and outside Git.")
    print(f"TikTok account identity recorded: {open_id}")
    return 0
