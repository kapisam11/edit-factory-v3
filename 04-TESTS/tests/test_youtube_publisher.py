import pytest

from ai_video_factory.youtube_publisher import fetch_video_statistics, upload_video


def test_upload_requires_client_secrets(monkeypatch, tmp_path):
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRETS", raising=False)
    with pytest.raises(ValueError, match="YOUTUBE_CLIENT_SECRETS"):
        upload_video(
            str(tmp_path / "missing.mp4"),
            title="Test",
            description="Test",
        )


def test_statistics_requires_client_secrets(monkeypatch):
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRETS", raising=False)
    with pytest.raises(ValueError, match="YOUTUBE_CLIENT_SECRETS"):
        fetch_video_statistics("abc123")
