from __future__ import annotations

from pathlib import Path

import pytest

from ai_video_factory import media_host
from ai_video_factory.media_host import MediaHostError, S3CompatibleMediaHost


def _configure_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIVF_MEDIA_HOST_S3_ENDPOINT", "https://example-account.r2.cloudflarestorage.com")
    monkeypatch.setenv("AIVF_MEDIA_HOST_S3_BUCKET", "edit-factory-media")
    monkeypatch.setenv("AIVF_MEDIA_HOST_S3_REGION", "auto")
    monkeypatch.setenv("AIVF_MEDIA_HOST_S3_ACCESS_KEY", "test-access-key")
    monkeypatch.setenv("AIVF_MEDIA_HOST_S3_SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("AIVF_MEDIA_HOST_S3_PREFIX", "edit-factory-tests")
    monkeypatch.setenv("AIVF_MEDIA_HOST_URL_TTL_SECONDS", "3600")


def test_media_host_requires_explicit_storage_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "AIVF_MEDIA_HOST_S3_ENDPOINT",
        "AIVF_MEDIA_HOST_S3_BUCKET",
        "AIVF_MEDIA_HOST_S3_ACCESS_KEY",
        "AIVF_MEDIA_HOST_S3_SECRET_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(MediaHostError) as caught:
        S3CompatibleMediaHost.from_environment()
    assert caught.value.code == "MISSING_MEDIA_HOST"
    assert caught.value.retryable is False


def test_media_host_upload_uses_sigv4_and_returns_a_time_limited_https_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_host(monkeypatch)
    video = tmp_path / "clip.mp4"
    data = b"media-host-signature-fixture"
    video.write_bytes(data)
    source_hash = media_host.hashlib.sha256(data).hexdigest()
    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200

    def put(url: str, **kwargs: object) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(media_host.requests, "put", put)
    host = S3CompatibleMediaHost.from_environment()
    hosted = host.upload_for_publication(
        video,
        platform="instagram",
        account_id="brand",
        publication_id="publication-42",
        source_sha256=source_hash,
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["url"].startswith("https://example-account.r2.cloudflarestorage.com/edit-factory-media/")
    headers = call["headers"]
    assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=test-access-key/")
    assert "Signature=" in headers["Authorization"]
    assert headers["x-amz-content-sha256"] == source_hash
    assert headers["Content-Length"] == str(len(data))
    assert hosted.url.startswith("https://example-account.r2.cloudflarestorage.com/edit-factory-media/")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in hosted.url
    assert "X-Amz-Signature=" in hosted.url
    assert "test-secret-key" not in hosted.url
    assert hosted.object_key.startswith("edit-factory-tests/instagram/brand/")
    assert hosted.expires_at > 0


def test_media_host_reuses_deterministic_object_key_and_caps_signed_url_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure_host(monkeypatch)
    monkeypatch.setenv("AIVF_MEDIA_HOST_URL_TTL_SECONDS", "999999")
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"repeatable content")
    source_hash = media_host.hashlib.sha256(video.read_bytes()).hexdigest()
    host = S3CompatibleMediaHost.from_environment()
    first = host.object_key_for(
        platform="instagram",
        account_id="brand",
        publication_id="publication-42",
        source_sha256=source_hash,
        suffix=".mp4",
    )
    second = host.object_key_for(
        platform="instagram",
        account_id="brand",
        publication_id="publication-42",
        source_sha256=source_hash,
        suffix=".mp4",
    )
    assert first == second
    presigned = host.presign_get(first)
    assert "X-Amz-Expires=3600" in presigned.url
    assert presigned.expires_at > 0


def test_media_host_rejects_non_https_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_host(monkeypatch)
    monkeypatch.setenv("AIVF_MEDIA_HOST_S3_ENDPOINT", "http://localhost:9000")
    with pytest.raises(MediaHostError) as caught:
        S3CompatibleMediaHost.from_environment()
    assert caught.value.code == "INVALID_MEDIA_HOST_CONFIGURATION"
