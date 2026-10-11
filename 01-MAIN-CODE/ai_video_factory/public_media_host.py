"""Optional S3-compatible hosting for Instagram's public-video-URL publishing API.

This module only publishes a video URL after the object has been uploaded and its
public URL returns HTTP 200. It never opens a port on the user's PC.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

import requests


_REQUIRED_ENV = (
    "AIVF_MEDIA_S3_ENDPOINT",
    "AIVF_MEDIA_S3_BUCKET",
    "AIVF_MEDIA_S3_ACCESS_KEY_ID",
    "AIVF_MEDIA_S3_SECRET_ACCESS_KEY",
    "AIVF_MEDIA_PUBLIC_BASE_URL",
)


def is_configured() -> bool:
    """Return true only when all required public-media hosting settings are present."""
    return all(os.environ.get(name, "").strip() for name in _REQUIRED_ENV)


def _hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _settings() -> dict[str, str]:
    values = {name: os.environ.get(name, "").strip() for name in _REQUIRED_ENV}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            "Instagram local-file hosting is not configured; missing: " + ", ".join(missing)
        )
    endpoint = urlparse(values["AIVF_MEDIA_S3_ENDPOINT"])
    public_base = urlparse(values["AIVF_MEDIA_PUBLIC_BASE_URL"])
    if (
        endpoint.scheme != "https"
        or not endpoint.netloc
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("AIVF_MEDIA_S3_ENDPOINT must be an HTTPS S3-compatible endpoint")
    if (
        public_base.scheme != "https"
        or not public_base.netloc
        or public_base.username
        or public_base.password
        or public_base.query
        or public_base.fragment
    ):
        raise ValueError("AIVF_MEDIA_PUBLIC_BASE_URL must be an HTTPS public URL base")
    bucket = values["AIVF_MEDIA_S3_BUCKET"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,62}", bucket):
        raise ValueError("AIVF_MEDIA_S3_BUCKET contains unsupported characters")
    region = os.environ.get("AIVF_MEDIA_S3_REGION", "auto").strip() or "auto"
    prefix = os.environ.get("AIVF_MEDIA_S3_PREFIX", "edit-factory-v3").strip().strip("/")
    if not prefix or not re.fullmatch(r"[A-Za-z0-9/_-]+", prefix) or ".." in prefix.split("/"):
        raise ValueError("AIVF_MEDIA_S3_PREFIX must contain only safe path components")
    return {
        "endpoint": values["AIVF_MEDIA_S3_ENDPOINT"].rstrip("/"),
        "bucket": bucket,
        "access_key": values["AIVF_MEDIA_S3_ACCESS_KEY_ID"],
        "secret_key": values["AIVF_MEDIA_S3_SECRET_ACCESS_KEY"],
        "public_base": values["AIVF_MEDIA_PUBLIC_BASE_URL"].rstrip("/"),
        "region": region,
        "prefix": prefix,
    }


def upload_public_video(video_path: str | Path, *, source_sha256: Optional[str] = None) -> str:
    """Upload one MP4 to a deterministic S3 object key and verify the public HTTPS URL."""
    path = Path(video_path)
    if not path.is_file() or path.is_symlink() or path.suffix.lower() != ".mp4":
        raise ValueError("public-media hosting accepts only regular, non-symlink MP4 files")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("cannot host an empty video")
    settings = _settings()
    digest = source_sha256 or _hash_file(path)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise ValueError("source_sha256 must be a SHA-256 hex digest")
    digest = digest.lower()
    object_key = f"{settings['prefix']}/{digest}.mp4"
    endpoint = urlparse(settings["endpoint"])
    canonical_uri = quote(
        f"{endpoint.path.rstrip('/')}/{settings['bucket']}/{object_key}".replace("//", "/"),
        safe="/-_.~",
    )
    request_url = f"{endpoint.scheme}://{endpoint.netloc}{canonical_uri}"
    public_url = f"{settings['public_base']}/{object_key}"
    public_parsed = urlparse(public_url)
    if public_parsed.scheme != "https" or not public_parsed.netloc:
        raise ValueError("derived public media URL is not HTTPS")

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    payload_hash = digest
    host = endpoint.netloc
    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_stamp}/{settings['region']}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    k_date = _hmac_sha256(("AWS4" + settings["secret_key"]).encode("utf-8"), date_stamp)
    k_region = hmac.new(k_date, settings["region"].encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={settings['access_key']}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers = {
        "Authorization": authorization,
        "Content-Type": "video/mp4",
        "Content-Length": str(size),
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    try:
        with path.open("rb") as stream:
            response = requests.put(
                request_url,
                headers=headers,
                data=stream,
                timeout=(15, 600),
            )
        if response.status_code not in {200, 201, 204}:
            raise RuntimeError(
                f"S3-compatible media upload failed with HTTP {response.status_code}; "
                "the video has not been confirmed publicly available"
            )
    except requests.RequestException as exc:
        raise RuntimeError(
            "S3-compatible media upload outcome could not be verified; retrying the same "
            "content hash uses the same object key"
        ) from exc

    try:
        check = requests.head(public_url, allow_redirects=False, timeout=(10, 20))
    except requests.RequestException as exc:
        raise RuntimeError(
            "Video object was uploaded but the configured public URL could not be verified"
        ) from exc
    if check.status_code != 200:
        raise RuntimeError(
            f"Video object was uploaded but its public URL returned HTTP {check.status_code}; "
            "check bucket/public-domain policy before publishing"
        )
    content_length = check.headers.get("Content-Length")
    if content_length and content_length.isdigit() and int(content_length) != size:
        raise RuntimeError("Public media URL returned a different content length; refusing Instagram publishing")
    content_type = check.headers.get("Content-Type", "").lower()
    if content_type and "video" not in content_type and "octet-stream" not in content_type:
        raise RuntimeError("Public media URL does not advertise video content; refusing Instagram publishing")
    return public_url


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
