"""Protected, refresh-aware access-token storage for social platform adapters.

Initial authorization remains a human/developer-app setup step. Once valid tokens
and app credentials are provisioned, rotating refresh tokens are persisted outside
the repository and refreshed before expiry.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import requests


class TokenRefreshError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


_SECURED_DIRECTORIES: set[str] = set()


def _protect_token_directory(directory: Path) -> None:
    resolved = directory.resolve()
    key = str(resolved)
    if key in _SECURED_DIRECTORIES:
        return
    resolved.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        identity = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
        if not identity.strip("\\"):
            raise OSError("Windows user identity is not available to secure platform tokens")
        try:
            result = subprocess.run(
                [
                    "icacls",
                    str(resolved),
                    "/inheritance:r",
                    "/grant:r",
                    f"{identity}:(OI)(CI)F",
                    "/grant:r",
                    "SYSTEM:(OI)(CI)F",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise OSError("could not restrict the Windows platform-token directory ACL") from exc
        if result.returncode != 0:
            raise OSError("could not restrict the Windows platform-token directory ACL")
    else:
        os.chmod(resolved, 0o700)
    _SECURED_DIRECTORIES.add(key)


def _state_database() -> Path:
    configured = os.environ.get("AIVF_PLATFORM_TOKEN_DB", "").strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        state_dir = Path(os.environ.get("AIVF_STATE_DIR", "./state")).expanduser()
        path = state_dir / "platform-secrets" / "platform-tokens.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    _protect_token_directory(path.parent)
    return path.resolve()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=20)
    connection.execute("PRAGMA busy_timeout=20000")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_tokens (
            platform TEXT NOT NULL,
            account_id TEXT NOT NULL,
            access_token TEXT NOT NULL,
            refresh_token TEXT NOT NULL DEFAULT '',
            expires_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            PRIMARY KEY(platform, account_id)
        )
        """
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows access is governed by the ACL inherited from the state folder.
        pass
    return connection


def _response_payload(response: requests.Response, *, platform: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if not response.ok:
        retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
        raise TokenRefreshError(
            f"{platform} token refresh returned HTTP {response.status_code}",
            code=f"AUTH_REFRESH_HTTP_{response.status_code}",
            retryable=retryable,
        )
    if payload.get("error"):
        error_value = payload["error"]
        if isinstance(error_value, dict):
            error_code = str(error_value.get("code") or error_value.get("type") or "AUTH_REFRESH_FAILED")
        else:
            error_code = "AUTH_REFRESH_FAILED"
        raise TokenRefreshError(
            f"{platform} token refresh was rejected ({error_code[:80]})",
            code="AUTH_REFRESH_REJECTED",
            retryable=False,
        )
    return payload


def _refresh_tiktok(
    *,
    refresh_token: str,
    client_key: str,
    client_secret: str,
) -> tuple[str, str, float]:
    try:
        response = requests.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise TokenRefreshError(
            f"TikTok token refresh network failure ({type(exc).__name__})",
            code="AUTH_REFRESH_NETWORK_ERROR",
            retryable=True,
        ) from exc
    payload = _response_payload(response, platform="TikTok")
    access = str(payload.get("access_token") or "").strip()
    rotated_refresh = str(payload.get("refresh_token") or "").strip()
    try:
        lifetime = int(payload.get("expires_in") or 0)
    except (TypeError, ValueError, OverflowError):
        lifetime = 0
    if not access or lifetime <= 0:
        raise TokenRefreshError(
            "TikTok refresh response omitted a valid access token or expiry",
            code="AUTH_REFRESH_INVALID_RESPONSE",
            retryable=False,
        )
    return access, rotated_refresh or refresh_token, time.time() + lifetime


def _refresh_instagram(
    *,
    access_token: str,
    app_id: str,
    app_secret: str,
    graph_api_version: str,
) -> tuple[str, str, float]:
    url = f"https://graph.facebook.com/{graph_api_version}/oauth/access_token"
    try:
        response = requests.get(
            url,
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": access_token,
            },
            timeout=20,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise TokenRefreshError(
            f"Instagram token refresh network failure ({type(exc).__name__})",
            code="AUTH_REFRESH_NETWORK_ERROR",
            retryable=True,
        ) from exc
    payload = _response_payload(response, platform="Instagram")
    access = str(payload.get("access_token") or "").strip()
    try:
        lifetime = int(payload.get("expires_in") or 0)
    except (TypeError, ValueError, OverflowError):
        lifetime = 0
    if not access or lifetime <= 0:
        raise TokenRefreshError(
            "Instagram token exchange omitted a valid access token or expiry",
            code="AUTH_REFRESH_INVALID_RESPONSE",
            retryable=False,
        )
    # Facebook Login exchanges the current access token for a new long-lived
    # token; that new token is itself the credential supplied to the next exchange.
    return access, "", time.time() + lifetime


def get_platform_access_token(
    *,
    platform: str,
    account_id: str,
    access_token: str = "",
    refresh_token: str = "",
    client_key: str = "",
    client_secret: str = "",
    app_id: str = "",
    app_secret: str = "",
    graph_api_version: str = "",
    refresh_before_seconds: int | None = None,
) -> str:
    """Return a usable token, refreshing and atomically persisting it when configured."""
    normalized = platform.strip().lower()
    if normalized not in {"instagram", "tiktok"}:
        raise ValueError(f"unsupported token refresh platform: {platform}")
    path = _state_database()
    now = time.time()
    if refresh_before_seconds is None:
        refresh_before_seconds = 7 * 24 * 60 * 60 if normalized == "instagram" else 60 * 60
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT access_token,refresh_token,expires_at FROM platform_tokens "
            "WHERE platform=? AND account_id=?",
            (normalized, account_id),
        ).fetchone()
        stored_access = str(row[0]) if row else ""
        stored_refresh = str(row[1]) if row else ""
        expires_at = float(row[2]) if row else 0.0

        source_access = stored_access or access_token.strip()
        source_refresh = stored_refresh or refresh_token.strip()
        refresh_configured = (
            bool(source_refresh and client_key and client_secret)
            if normalized == "tiktok"
            else bool(source_access and app_id and app_secret and graph_api_version)
        )

        if row and expires_at > now + max(0, int(refresh_before_seconds)):
            connection.commit()
            return stored_access

        # Access-token-only configurations remain supported for manual rotation.
        # A managed, stored token with a known expiry must not silently be used
        # after expiry if refresh credentials are missing or invalid.
        if not refresh_configured:
            if row and expires_at > now:
                connection.commit()
                return stored_access
            if row and expires_at > 0:
                connection.rollback()
                raise TokenRefreshError(
                    f"{normalized} credentials expired and refresh credentials are not configured",
                    code="AUTH_REFRESH_REQUIRED",
                    retryable=False,
                )
            if access_token.strip():
                connection.commit()
                return access_token.strip()
            connection.rollback()
            raise TokenRefreshError(
                f"{normalized} access token is missing for account {account_id!r}",
                code="MISSING_CREDENTIALS",
                retryable=False,
            )

        try:
            if normalized == "tiktok":
                new_access, new_refresh, new_expiry = _refresh_tiktok(
                    refresh_token=source_refresh,
                    client_key=client_key,
                    client_secret=client_secret,
                )
            else:
                new_access, new_refresh, new_expiry = _refresh_instagram(
                    access_token=source_access,
                    app_id=app_id,
                    app_secret=app_secret,
                    graph_api_version=graph_api_version,
                )
        except TokenRefreshError:
            connection.rollback()
            raise
        connection.execute(
            """
            INSERT INTO platform_tokens
                (platform,account_id,access_token,refresh_token,expires_at,updated_at)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(platform,account_id) DO UPDATE SET
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                expires_at=excluded.expires_at,
                updated_at=excluded.updated_at
            """,
            (normalized, account_id, new_access, new_refresh, new_expiry, now),
        )
        connection.commit()
        return new_access


__all__ = ["TokenRefreshError", "get_platform_access_token"]
