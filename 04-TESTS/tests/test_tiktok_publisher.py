from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import ai_video_factory.tiktok_publisher as tiktok


def _configure(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AIVF_TIKTOK_ACCESS_TOKEN", "secret-test-token")
    monkeypatch.setenv("AIVF_TIKTOK_OPEN_ID", "open-test-id")
    monkeypatch.setenv("AIVF_TIKTOK_IDEMPOTENCY_DIR", str(tmp_path / "journal"))
    monkeypatch.delenv("AIVF_TIKTOK_TOKEN_PATH", raising=False)
    monkeypatch.setattr(
        tiktok,
        "_load_token",
        lambda: ("secret-test-token", {"access_token": "secret-test-token", "open_id": "open-test-id"}),
    )


def test_privacy_mapping_rejects_unlisted():
    assert tiktok._privacy_level("private") == "SELF_ONLY"
    assert tiktok._privacy_level("public") == "PUBLIC_TO_EVERYONE"
    with pytest.raises(ValueError, match="unlisted"):
        tiktok._privacy_level("unlisted")


def test_direct_post_upload_persists_remote_id_and_is_idempotent(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    video = tmp_path / "story.mp4"
    video.write_bytes(b"video bytes")
    monkeypatch.setattr(
        tiktok.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="30.0"),
        raising=False,
    )
    calls = []

    def fake_api(method, url, *, token, body=None, params=None):
        calls.append((method, url))
        if url == tiktok.CREATOR_INFO_URL:
            return {"data": {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 60}}
        if url == tiktok.INIT_URL:
            return {"data": {"publish_id": "publish-123", "upload_url": "https://upload.tiktok.com/session"}}
        if url == tiktok.STATUS_URL:
            return {"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["post-456"]}}
        raise AssertionError(f"unexpected API call: {method} {url}")

    def fake_upload(path, *, token, record, journal_path):
        record["uploaded_bytes"] = Path(path).stat().st_size
        record["state"] = "processing"
        tiktok._write_json(journal_path, record)

    monkeypatch.setattr(tiktok, "_api_json", fake_api)
    monkeypatch.setattr(tiktok, "_upload_chunks", fake_upload)

    result = tiktok.publish_video(
        str(video),
        title="A real title",
        description="A short description",
        tags=["demo"],
        privacy_status="private",
        idempotency_key="queue-job-123",
        idempotency_directory=str(tmp_path / "journal"),
        max_poll_seconds=0,
    )

    assert result["video_id"] == "post-456"
    assert result["publish_id"] == "publish-123"
    assert result["status"] == "PUBLISH_COMPLETE"
    assert result["url"] == "https://www.tiktok.com/@open-test-id/video/post-456"
    assert len(calls) == 3

    # A repeat with the same key returns the journal result without another API call.
    repeated = tiktok.publish_video(
        str(video),
        title="A real title",
        description="A short description",
        tags=["demo"],
        privacy_status="private",
        idempotency_key="queue-job-123",
        idempotency_directory=str(tmp_path / "journal"),
        max_poll_seconds=0,
    )
    assert repeated == result
    assert len(calls) == 3


def test_ambiguous_init_persists_intent_and_refuses_second_init(tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    video = tmp_path / "story.mp4"
    video.write_bytes(b"video bytes")
    monkeypatch.setattr(
        tiktok.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout="30.0"),
        raising=False,
    )
    init_calls = []

    def fake_api(method, url, *, token, body=None, params=None):
        if url == tiktok.CREATOR_INFO_URL:
            return {"data": {"privacy_level_options": ["SELF_ONLY"], "max_video_post_duration_sec": 60}}
        if url == tiktok.INIT_URL:
            init_calls.append(url)
            raise RuntimeError("TikTok API request failed before a response could be verified")
        raise AssertionError(f"unexpected API call: {method} {url}")

    monkeypatch.setattr(tiktok, "_api_json", fake_api)

    with pytest.raises(RuntimeError, match="initialization outcome is uncertain"):
        tiktok.publish_video(
            str(video),
            title="A title",
            description="",
            privacy_status="private",
            idempotency_key="uncertain-job",
            idempotency_directory=str(tmp_path / "journal"),
            max_poll_seconds=0,
        )

    records = list((tmp_path / "journal").glob("*.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding="utf-8"))["state"] == "init_started"

    with pytest.raises(RuntimeError, match="no publish_id"):
        tiktok.publish_video(
            str(video),
            title="A title",
            description="",
            privacy_status="private",
            idempotency_key="uncertain-job",
            idempotency_directory=str(tmp_path / "journal"),
            max_poll_seconds=0,
        )
    assert len(init_calls) == 1


def test_api_errors_do_not_echo_access_token(monkeypatch):
    class Response:
        status_code = 400
        ok = False

        @staticmethod
        def json():
            return {"error": {"code": "invalid_token", "message": "secret-test-token is invalid"}}

    monkeypatch.setattr(tiktok.requests, "request", lambda *a, **k: Response())
    with pytest.raises(RuntimeError) as error:
        tiktok._api_json("POST", tiktok.STATUS_URL, token="secret-test-token", body={"publish_id": "x"})
    assert "secret-test-token" not in str(error.value)
