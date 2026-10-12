from __future__ import annotations

import importlib
import time
from pathlib import Path

from ai_video_factory.multi_platform_publishing import PublicationStore, Target
from ai_video_factory.platform_adapters import PublishResult


def _load_client(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AIVF_ENV", "test")
    state = tmp_path / "state"
    database = state / "folder-publisher.sqlite"
    monkeypatch.setenv("AIVF_STATE_DIR", str(state))
    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIVF_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("AIVF_FOLDER_STATE_DB", str(database))
    monkeypatch.setenv("AIVF_DASHBOARD_TOKEN", "test-dashboard-token")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-dashboard-session-key")
    monkeypatch.setenv("AIVF_COOKIE_SECURE", "0")
    import web_app_v3

    importlib.reload(web_app_v3)
    web_app_v3.app.config["TESTING"] = True
    from dashboard_auth import configure_dashboard_auth

    configure_dashboard_auth(web_app_v3.app)
    return web_app_v3.app.test_client(), database


def test_publishing_status_is_authenticated_and_handles_missing_queue(monkeypatch, tmp_path):
    client, database = _load_client(monkeypatch, tmp_path)
    assert not database.exists()
    denied = client.get("/api/publishing/status")
    assert denied.status_code == 401

    with client.session_transaction() as session:
        session["aivf_authenticated"] = True
    response = client.get("/api/publishing/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "not_configured"
    assert payload["database_configured"] is False
    assert payload["destinations"] == []


def test_publishing_status_reports_remote_results_and_queue_state(monkeypatch, tmp_path):
    client, database = _load_client(monkeypatch, tmp_path)
    source = tmp_path / "clip.mp4"
    staged = tmp_path / "stage" / "clip.mp4"
    source.write_bytes(b"source")
    staged.parent.mkdir()
    staged.write_bytes(b"source")

    store = PublicationStore(database)
    now = time.time()
    job_id, created = store.enqueue(
        publication_id="dashboard-status-job",
        item={
            "id": 1,
            "path": str(source),
            "size_bytes": source.stat().st_size,
            "mtime_ns": source.stat().st_mtime_ns,
            "sidecar_mtime_ns": None,
        },
        source_sha256="a" * 64,
        staged_path=staged,
        target=Target("youtube", "default"),
        metadata={"title": "Test"},
        max_attempts=4,
        now=now,
    )
    assert created
    claim = store.claim_next(max_per_day=2, now=now + 1)
    assert claim is not None and claim["id"] == job_id
    store.mark_published(
        job_id,
        str(claim["lease_owner"]),
        PublishResult(
            "youtube",
            "remote-video-123",
            "remote-video-123",
            "https://www.youtube.com/watch?v=remote-video-123",
        ),
        now=now + 2,
    )

    with client.session_transaction() as session:
        session["aivf_authenticated"] = True
    response = client.get("/api/publishing/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["counts_by_state"]["published"] == 1
    assert payload["destinations"][0]["job_id"] == job_id
    assert payload["destinations"][0]["remote_post_id"] == "remote-video-123"
    assert payload["destinations"][0]["remote_url"] == "https://www.youtube.com/watch?v=remote-video-123"
    assert payload["destinations"][0]["events"][0]["event_type"] == "published"
    assert payload["destinations"][0]["events"][0]["state"] == "published"
    assert payload["last_success_epoch"] == now + 2

def test_dashboard_exposes_live_publishing_health_and_destination_results(monkeypatch, tmp_path):
    client, _database = _load_client(monkeypatch, tmp_path)
    with client.session_transaction() as session:
        session["aivf_authenticated"] = True

    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="publishingPanel"' in html
    assert 'id="publishingStatus"' in html
    assert 'id="publishingDestinations"' in html
    assert "refreshPublishingStatus" in html
    assert "/api/publishing/status" in html
    assert "Last confirmed publication" in html
    assert "Token" in html or "token" in html


def test_dashboard_marks_publications_after_deadline_as_missed(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_REQUIRE_DAILY_PUBLISH", "1")
    monkeypatch.setenv("AIVF_PUBLISH_DEADLINE_HOUR", "0")
    monkeypatch.setenv("AIVF_PUBLISH_DEADLINE_MINUTE", "0")
    client, database = _load_client(monkeypatch, tmp_path)
    source = tmp_path / "late-clip.mp4"
    staged = tmp_path / "stage" / "late-clip.mp4"
    source.write_bytes(b"late source")
    staged.parent.mkdir()
    staged.write_bytes(b"late source")

    store = PublicationStore(database)
    now = time.time()
    job_id, created = store.enqueue(
        publication_id="dashboard-late-deadline",
        item={
            "id": 1,
            "path": str(source),
            "size_bytes": source.stat().st_size,
            "mtime_ns": source.stat().st_mtime_ns,
            "sidecar_mtime_ns": None,
        },
        source_sha256="b" * 64,
        staged_path=staged,
        target=Target("youtube", "default"),
        metadata={"title": "Late publication"},
        max_attempts=3,
        now=now - 10,
    )
    assert created
    claimed = store.claim_next(max_per_day=2, now=now - 9)
    assert claimed is not None and claimed["id"] == job_id
    store.mark_published(
        job_id,
        str(claimed["lease_owner"]),
        PublishResult(
            "youtube",
            "late-remote-id",
            "late-remote-id",
            "https://www.youtube.com/watch?v=late-remote-id",
        ),
        now=now,
    )

    with client.session_transaction() as session:
        session["aivf_authenticated"] = True
    response = client.get("/api/publishing/status")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["deadline"]["required"] is True
    assert payload["deadline"]["missed_today"] is True
