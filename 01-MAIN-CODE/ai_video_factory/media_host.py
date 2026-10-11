"""Optional S3-compatible media hosting for platform APIs that pull public URLs.

Credentials are sourced only from environment variables. Uploads use SigV4-signed
PUTs and Instagram receives a time-limited HTTPS presigned GET URL. No credentials
or signed URLs are written to logs by this module.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import requests


class MediaHostError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    def sign(key: bytes, value: str) -> bytes:
        import hmac

        return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()

    key_date = sign(("AWS4" + secret).encode("utf-8"), date_stamp)
    key_region = sign(key_date, region)
    key_service = sign(key_region, "s3")
    return sign(key_service, "aws4_request")


def _canonical_uri(bucket: str, key: str) -> str:
    encoded_bucket = quote(bucket, safe="-_.~")
    encoded_key = "/".join(quote(part, safe="-_.~") for part in key.split("/"))
    return f"/{encoded_bucket}/{encoded_key}"


def _canonical_query(parameters: dict[str, str]) -> str:
    encoded = [
        (quote(key, safe="-_.~"), quote(value, safe="-_.~"))
        for key, value in parameters.items()
    ]
    encoded.sort()
    return "&".join(f"{key}={value}" for key, value in encoded)


@dataclass(frozen=True)
class HostedMedia:
    object_key: str
    url: str
    expires_at: float


@dataclass(frozen=True)
class S3CompatibleMediaHost:
    endpoint: str
    bucket: str
    region: str
    access_key: str
    secret_key: str
    prefix: str = "edit-factory"
    url_ttl_seconds: int = 3600

    @classmethod
    def from_environment(cls) -> "S3CompatibleMediaHost":
        endpoint = os.environ.get("AIVF_MEDIA_HOST_S3_ENDPOINT", "").strip().rstrip("/")
        bucket = os.environ.get("AIVF_MEDIA_HOST_S3_BUCKET", "").strip()
        region = os.environ.get("AIVF_MEDIA_HOST_S3_REGION", "auto").strip() or "auto"
        access_key = os.environ.get("AIVF_MEDIA_HOST_S3_ACCESS_KEY", "").strip()
        secret_key = os.environ.get("AIVF_MEDIA_HOST_S3_SECRET_KEY", "").strip()
        prefix = os.environ.get("AIVF_MEDIA_HOST_S3_PREFIX", "edit-factory").strip().strip("/")
        try:
            ttl = int(os.environ.get("AIVF_MEDIA_HOST_URL_TTL_SECONDS", "3600"))
        except ValueError:
            ttl = 3600

        if not endpoint or not bucket or not access_key or not secret_key:
            raise MediaHostError(
                "Instagram Reels needs a public video_url or S3-compatible media hosting; set AIVF_MEDIA_HOST_S3_ENDPOINT, BUCKET, ACCESS_KEY and SECRET_KEY",
                code="MISSING_MEDIA_HOST",
                retryable=False,
            )
        try:
            parsed = urlsplit(endpoint)
        except ValueError as exc:
            raise MediaHostError(
                "media-host endpoint is invalid",
                code="INVALID_MEDIA_HOST_CONFIGURATION",
                retryable=False,
            ) from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise MediaHostError(
                "media-host endpoint must be a base HTTPS URL without credentials, path, query or fragment",
                code="INVALID_MEDIA_HOST_CONFIGURATION",
                retryable=False,
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{1,61}[A-Za-z0-9]", bucket):
            raise MediaHostError(
                "media-host bucket name is invalid",
                code="INVALID_MEDIA_HOST_CONFIGURATION",
                retryable=False,
            )
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", region):
            raise MediaHostError(
                "media-host signing region is invalid",
                code="INVALID_MEDIA_HOST_CONFIGURATION",
                retryable=False,
            )
        if prefix and any(part in {"", ".", ".."} for part in prefix.split("/")):
            raise MediaHostError(
                "media-host prefix contains an unsafe path component",
                code="INVALID_MEDIA_HOST_CONFIGURATION",
                retryable=False,
            )
        return cls(
            endpoint=endpoint,
            bucket=bucket,
            region=region,
            access_key=access_key,
            secret_key=secret_key,
            prefix=prefix or "edit-factory",
            url_ttl_seconds=max(300, min(ttl, 3600)),
        )

    @property
    def host(self) -> str:
        return urlsplit(self.endpoint).netloc.lower()

    def _url(self, key: str, *, query: str = "") -> str:
        uri = _canonical_uri(self.bucket, key)
        return urlunsplit(("https", self.host, uri, query, ""))

    def presign_get(self, object_key: str, *, now: datetime | None = None) -> HostedMedia:
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        amz_date = instant.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = instant.strftime("%Y%m%d")
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        parameters = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(self.url_ttl_seconds),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_request = "\n".join(
            (
                "GET",
                _canonical_uri(self.bucket, object_key),
                _canonical_query(parameters),
                f"host:{self.host}\n",
                "host",
                "UNSIGNED-PAYLOAD",
            )
        )
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            )
        )
        import hmac

        signature = hmac.new(
            _signing_key(self.secret_key, date_stamp, self.region),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        query = _canonical_query({**parameters, "X-Amz-Signature": signature})
        return HostedMedia(
            object_key=object_key,
            url=self._url(object_key, query=query),
            expires_at=instant.timestamp() + self.url_ttl_seconds,
        )

    def object_key_for(
        self,
        *,
        platform: str,
        account_id: str,
        publication_id: str,
        source_sha256: str,
        suffix: str,
    ) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
            raise MediaHostError(
                "source SHA-256 is required for deterministic media hosting",
                code="INVALID_SOURCE_HASH",
                retryable=False,
            )
        safe_account = re.sub(r"[^A-Za-z0-9_.-]", "_", account_id)[:64] or "default"
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", publication_id):
            raise MediaHostError(
                "publication ID contains unsafe characters for media hosting",
                code="INVALID_PUBLICATION_ID",
                retryable=False,
            )
        normalized_suffix = suffix.lower() if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix or "") else ".mp4"
        parts = [self.prefix, platform.lower(), safe_account, source_sha256[:2],
                 f"{publication_id}-{source_sha256}{normalized_suffix}"]
        return "/".join(part for part in parts if part)

    def upload_file(
        self,
        media_path: Path,
        *,
        object_key: str,
        source_sha256: str,
    ) -> HostedMedia:
        size_bytes = media_path.stat().st_size
        if size_bytes <= 0 or size_bytes > 5_000_000_000:
            raise MediaHostError(
                "single-request S3-compatible hosting requires a non-empty file no larger than 5 GB",
                code="MEDIA_HOST_SIZE_LIMIT",
                retryable=False,
            )
        mime = mimetypes.guess_type(media_path.name)[0]
        if mime not in {"video/mp4", "video/quicktime", "video/webm"}:
            mime = {
                ".mp4": "video/mp4",
                ".mov": "video/quicktime",
                ".webm": "video/webm",
            }.get(media_path.suffix.lower(), "application/octet-stream")
        instant = datetime.now(timezone.utc)
        amz_date = instant.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = instant.strftime("%Y%m%d")
        payload_hash = source_sha256.lower()
        uri = _canonical_uri(self.bucket, object_key)
        canonical_headers = (
            f"content-type:{mime}\n"
            f"host:{self.host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            (
                "PUT",
                uri,
                "",
                canonical_headers,
                signed_headers,
                payload_hash,
            )
        )
        scope = f"{date_stamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            )
        )
        import hmac

        signature = hmac.new(
            _signing_key(self.secret_key, date_stamp, self.region),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        url = self._url(object_key)
        headers = {
            "Authorization": authorization,
            "Content-Type": mime,
            "Content-Length": str(size_bytes),
            "Host": self.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        try:
            with media_path.open("rb") as handle:
                response = requests.put(
                    url,
                    headers=headers,
                    data=handle,
                    timeout=(10, 180),
                    allow_redirects=False,
                )
        except (OSError, requests.RequestException) as exc:
            raise MediaHostError(
                f"media hosting request failed ({type(exc).__name__})",
                code="MEDIA_HOST_NETWORK_ERROR",
                retryable=True,
            ) from exc
        if response.status_code not in {200, 201, 204}:
            retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
            raise MediaHostError(
                f"media host returned HTTP {response.status_code}",
                code=f"MEDIA_HOST_HTTP_{response.status_code}",
                retryable=retryable,
            )
        return self.presign_get(object_key)

    def upload_for_publication(
        self,
        media_path: Path,
        *,
        platform: str,
        account_id: str,
        publication_id: str,
        source_sha256: str,
    ) -> HostedMedia:
        object_key = self.object_key_for(
            platform=platform,
            account_id=account_id,
            publication_id=publication_id,
            source_sha256=source_sha256,
            suffix=media_path.suffix,
        )
        return self.upload_file(media_path, object_key=object_key, source_sha256=source_sha256)


__all__ = ["HostedMedia", "MediaHostError", "S3CompatibleMediaHost"]
