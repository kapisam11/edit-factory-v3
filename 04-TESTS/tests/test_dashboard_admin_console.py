"""Security and integration tests for the hidden owner/admin diagnostics console."""
from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import pytest


def _make_client(monkeypatch, tmp_path: Path, *, role: str = "admin"):
    monkeypatch.setenv("AIVF_ENV", "test")
    monkeypatch.setenv("AIVF_DASHBOARD_TOKEN", "dashboard-test-token")
    monkeypatch.setenv("AIVF_DASHBOARD_ROLE", role)
    monkeypatch.setenv("AIVF_ADMIN_PANEL_CODE", "2026")
    monkeypatch.setenv("AIVF_COOKIE_SECURE", "0")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-session-secret-that-is-long-enough-123456")
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIVF_OUTPUT_DIR", str(tmp_path / "output"))

    import web_app_v3
    import dashboard_auth
    import dashboard_compat

    importlib.reload(web_app_v3)
    importlib.reload(dashboard_auth)
    dashboard_auth.configure_dashboard_auth(web_app_v3.app)
    dashboard_auth._login_attempts.clear()
    importlib.reload(dashboard_compat)
    dashboard_compat.register_dashboard_compat(web_app_v3.app)
    web_app_v3.app.config["TESTING"] = True
    client = web_app_v3.app.test_client()
    response = client.post(
        "/login",
        data={"token": "dashboard-test-token"},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 302
    return client, web_app_v3


def test_admin_console_stays_hidden_until_server_unlock(monkeypatch, tmp_path):
    client, _app_module = _make_client(monkeypatch, tmp_path, role="admin")

    assert client.get("/admin").status_code == 404
    locked = client.get("/api/admin/diagnostics")
    assert locked.status_code == 403

    bad = client.post(
        "/api/admin/unlock",
        json={"code": "wrong"},
        headers={"Origin": "http://localhost"},
    )
    assert bad.status_code == 401

    unlocked = client.post(
        "/api/admin/unlock",
        json={"code": "2026"},
        headers={"Origin": "http://localhost"},
    )
    assert unlocked.status_code == 200
    assert unlocked.get_json()["expires_in_seconds"] == 900
    assert client.get("/admin").status_code == 200
    diagnostics = client.get("/api/admin/diagnostics")
    assert diagnostics.status_code == 200
    payload = diagnostics.get_json()
    assert payload["database"]["integrity_check"].lower() == "ok"
    assert "recent_failures" in payload
    assert "audit_events" in payload
    serialized = json.dumps(payload)
    assert "dashboard-test-token" not in serialized
    assert "test-session-secret-that-is-long-enough" not in serialized


def test_admin_unlock_code_does_not_grant_viewer_role_admin_access(monkeypatch, tmp_path):
    client, _app_module = _make_client(monkeypatch, tmp_path, role="viewer")

    response = client.post(
        "/api/admin/unlock",
        json={"code": "2026"},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 403
    assert client.get("/admin").status_code == 403
    assert client.get("/api/admin/diagnostics").status_code == 403


def test_settings_writes_require_admin_server_side(monkeypatch, tmp_path):
    client, _app_module = _make_client(monkeypatch, tmp_path, role="viewer")

    response = client.post(
        "/api/settings",
        json={"default_target_seconds": 30},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 403


def test_admin_console_unlock_expires(monkeypatch, tmp_path):
    client, app_module = _make_client(monkeypatch, tmp_path, role="admin")
    unlock = client.post(
        "/api/admin/unlock",
        json={"code": "2026"},
        headers={"Origin": "http://localhost"},
    )
    assert unlock.status_code == 200
    with client.session_transaction() as session:
        session[app_module._ADMIN_UNLOCK_SESSION_KEY] = time.time() - 1

    assert client.get("/admin").status_code == 404
    assert client.get("/api/admin/diagnostics").status_code == 403


def test_diagnostic_error_redaction_removes_common_credential_shapes(monkeypatch, tmp_path):
    _client, app_module = _make_client(monkeypatch, tmp_path, role="admin")
    text = app_module._redact_diagnostic_text(
        "model_api_key=supersecret Bearer token-value Authorization: hidden"
    )
    assert "supersecret" not in text
    assert "token-value" not in text
    assert "hidden" not in text
    assert "[REDACTED]" in text


def test_dashboard_pages_render_for_authenticated_users(monkeypatch, tmp_path):
    client, _app_module = _make_client(monkeypatch, tmp_path, role="admin")
    for path in ("/", "/jobs", "/library", "/settings"):
        response = client.get(path)
        assert response.status_code == 200, path
    html = client.get("/jobs").get_data(as_text=True)
    assert 'id="jobsTable"' in html
    assert 'href="/library"' in html
