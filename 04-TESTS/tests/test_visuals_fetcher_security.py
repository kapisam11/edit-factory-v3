import pytest

from ai_video_factory import visuals_fetcher


def test_validate_remote_url_rejects_non_https():
    with pytest.raises(ValueError, match="HTTPS"):
        visuals_fetcher.validate_remote_url("http://upload.wikimedia.org/test.jpg")


def test_validate_remote_url_rejects_unallowlisted_host():
    with pytest.raises(ValueError, match="host is not allowed"):
        visuals_fetcher.validate_remote_url("https://example.com/test.jpg")


def test_validate_remote_url_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(
        visuals_fetcher.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(0, 0, 0, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="non-public IP"):
        visuals_fetcher.validate_remote_url("https://upload.wikimedia.org/test.jpg")


def test_download_youtube_clip_rejects_non_youtube_url(tmp_path):
    with pytest.raises(ValueError, match="host is not allowed"):
        visuals_fetcher.download_youtube_clip("https://example.com/video", str(tmp_path))


def test_download_visuals_skips_untrusted_url(tmp_path, monkeypatch):
    def fail_request(*args, **kwargs):
        raise AssertionError("untrusted URL reached requests.get")

    monkeypatch.setattr(visuals_fetcher.requests, "get", fail_request)
    saved = visuals_fetcher.download_visuals(
        [{"url": "https://example.com/image.jpg", "source": "external"}],
        str(tmp_path),
        download_clips=False,
    )
    assert saved == []


def test_research_fallback_validation_allows_public_https(monkeypatch):
    monkeypatch.setattr(
        visuals_fetcher.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(0, 0, 0, "", ("93.184.216.34", 443))],
    )
    assert visuals_fetcher.validate_remote_url(
        "https://example.com/article", allowed_hosts=None
    ) == "https://example.com/article"


def test_safe_get_revalidates_redirect_target(monkeypatch):
    class Response:
        def __init__(self, status_code, location=None):
            self.status_code = status_code
            self.headers = {"Location": location} if location else {}

    calls = []
    responses = iter([Response(302, "https://example.com/private.jpg")])

    def fake_request(url, **kwargs):
        calls.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(visuals_fetcher.requests, "get", fake_request)
    monkeypatch.setattr(
        visuals_fetcher.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(0, 0, 0, "", ("93.184.216.34", 443))],
    )

    with pytest.raises(ValueError, match="host is not allowed"):
        visuals_fetcher._safe_get(
            "https://upload.wikimedia.org/test.jpg",
            allowed_hosts=visuals_fetcher._VISUAL_DOWNLOAD_HOSTS,
        )
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False
