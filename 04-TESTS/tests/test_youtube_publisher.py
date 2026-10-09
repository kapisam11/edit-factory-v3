import json
import sys
from types import ModuleType

import pytest

import ai_video_factory.youtube_publisher as publisher
from ai_video_factory.youtube_publisher import (
    _attach_uploaded_assets,
    _find_existing_video,
    _timestamp_epoch,
    fetch_video_statistics,
    upload_video,
)


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



class _FakeCall:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class _FakeResource:
    def __init__(self, list_handler=None, insert_handler=None):
        self.list_handler = list_handler
        self.insert_handler = insert_handler
        self.list_calls = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        if self.list_handler is None:
            raise AssertionError("unexpected list call")
        return _FakeCall(self.list_handler(kwargs))

    def insert(self, **kwargs):
        if self.insert_handler is None:
            raise AssertionError("unexpected insert call")
        return self.insert_handler(kwargs)


class _FakeYouTube:
    def __init__(self, *, channel_resource, playlist_resource, video_resource):
        self._channel_resource = channel_resource
        self._playlist_resource = playlist_resource
        self._video_resource = video_resource

    def channels(self):
        return self._channel_resource

    def playlistItems(self):
        return self._playlist_resource

    def videos(self):
        return self._video_resource


def test_idempotency_recovery_searches_private_uploads_and_ignores_older_match():
    channel_resource = _FakeResource(
        list_handler=lambda _kwargs: {
            "items": [{
                "id": "channel",
                "contentDetails": {
                    "relatedPlaylists": {"uploads": "uploads-playlist"}
                },
            }]
        }
    )
    playlist_resource = _FakeResource(
        list_handler=lambda kwargs: {
            "items": [
                {
                    "snippet": {
                        "publishedAt": "2026-10-09T13:00:00Z",
                        "resourceId": {"videoId": "fresh-private-video"},
                    },
                    "contentDetails": {"videoId": "fresh-private-video"},
                },
                {
                    "snippet": {
                        "publishedAt": "2026-10-09T10:00:00Z",
                        "resourceId": {"videoId": "old-video"},
                    },
                    "contentDetails": {"videoId": "old-video"},
                },
            ],
            "nextPageToken": "older-page",
        }
    )
    video_resource = _FakeResource(
        list_handler=lambda kwargs: {
            "items": [{
                "id": "fresh-private-video",
                "snippet": {
                    "title": "A genuinely new title",
                    "publishedAt": "2026-10-09T13:00:00Z",
                },
                "contentDetails": {"duration": "PT45S"},
                "status": {"privacyStatus": "private"},
            }]
        }
    )
    youtube = _FakeYouTube(
        channel_resource=channel_resource,
        playlist_resource=playlist_resource,
        video_resource=video_resource,
    )

    boundary = _timestamp_epoch("2026-10-09T12:00:00Z")
    assert boundary is not None
    found = _find_existing_video(
        youtube,
        title="A genuinely new title",
        duration_seconds=45.0,
        published_after=boundary,
    )

    assert found is not None
    assert found["id"] == "fresh-private-video"
    assert found["status"]["privacyStatus"] == "private"
    assert playlist_resource.list_calls[0]["playlistId"] == "uploads-playlist"
    assert video_resource.list_calls[0]["id"] == "fresh-private-video"


def test_retrying_uploaded_video_reapplies_thumbnail_without_duplicate_captions(tmp_path):
    thumbnail = tmp_path / "thumb.png"
    captions = tmp_path / "captions.srt"
    thumbnail.write_bytes(b"fake thumbnail")
    captions.write_text("1\\n00:00:00,000 --> 00:00:01,000\\nCaption\\n", encoding="utf-8")

    class _ThumbnailResource:
        def __init__(self):
            self.calls = []

        def set(self, **kwargs):
            self.calls.append(kwargs)
            return _FakeCall({"status": "thumbnail-set"})

    class _CaptionResource:
        def __init__(self):
            self.list_calls = []
            self.insert_calls = []

        def list(self, **kwargs):
            self.list_calls.append(kwargs)
            return _FakeCall({
                "items": [{
                    "id": "existing-caption",
                    "snippet": {"language": "en", "name": "Captions"},
                }]
            })

        def insert(self, **kwargs):
            self.insert_calls.append(kwargs)
            raise AssertionError("existing captions must not be inserted twice")

    class _YouTube:
        def __init__(self):
            self.thumbnail_resource = _ThumbnailResource()
            self.caption_resource = _CaptionResource()

        def thumbnails(self):
            return self.thumbnail_resource

        def captions(self):
            return self.caption_resource

    youtube = _YouTube()

    def _media_upload(path, **kwargs):
        return {"path": path, **kwargs}

    attached = _attach_uploaded_assets(
        youtube,
        "video-id",
        _media_upload,
        thumbnail_path=str(thumbnail),
        caption_path=str(captions),
        caption_language="en",
    )

    assert attached["thumbnail"]["status"] == "thumbnail-set"
    assert attached["captions"]["already_present"] is True
    assert youtube.caption_resource.list_calls[0]["videoId"] == "video-id"
    assert youtube.caption_resource.insert_calls == []
    assert len(youtube.thumbnail_resource.calls) == 1


def test_new_idempotency_key_does_not_match_an_unrelated_old_video(monkeypatch, tmp_path):
    video = tmp_path / "new.mp4"
    video.write_bytes(b"not a real video; ffprobe is mocked")
    idem_root = tmp_path / "idempotency"
    idem_root.mkdir()

    class _MediaFileUpload:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    http_module = ModuleType("googleapiclient.http")
    http_module.MediaFileUpload = _MediaFileUpload
    google_module = ModuleType("googleapiclient")
    google_module.http = http_module
    monkeypatch.setitem(sys.modules, "googleapiclient", google_module)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http_module)

    class _UploadRequest:
        def next_chunk(self):
            return None, {"id": "brand-new-video"}

    video_resource = _FakeResource(
        insert_handler=lambda _kwargs: _UploadRequest()
    )
    youtube = _FakeYouTube(
        channel_resource=_FakeResource(),
        playlist_resource=_FakeResource(),
        video_resource=video_resource,
    )
    monkeypatch.setattr(publisher, "_require_google", lambda: None)
    monkeypatch.setattr(publisher, "_paths", lambda client, token: ("client.json", "token.json"))
    monkeypatch.setattr(publisher, "_service", lambda *args, **kwargs: youtube)
    monkeypatch.setattr(publisher, "_idempotency_path", lambda key: idem_root / f"{key}.json")
    monkeypatch.setattr(
        publisher,
        "_find_existing_video",
        lambda **kwargs: pytest.fail("a first-time idempotency key must not search old uploads"),
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"stdout": "45.0"})(),
    )

    result = upload_video(
        str(video),
        title="A title that happened to be used before",
        description="Original new upload",
        client_secrets_path="client.json",
        token_path="token.json",
        idempotency_key="new-content-fingerprint",
    )

    assert result["video_id"] == "brand-new-video"
    assert result["deduplicated"] is False
    record = json.loads((idem_root / "new-content-fingerprint.json").read_text(encoding="utf-8"))
    assert record["status"] == "uploaded"
    assert record["video_id"] == "brand-new-video"
