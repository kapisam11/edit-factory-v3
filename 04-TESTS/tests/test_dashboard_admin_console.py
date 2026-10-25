"""Security and integration tests for the hidden owner/admin diagnostics console."""
from __future__ import annotations

import importlib
import json
import time
from pathlib import Path


def _make_client(
    monkeypatch,
    tmp_path: Path,
    *,
    role: str = "admin",
    users_json: str | None = None,
    login_token: str | None = "dashboard-test-token",
):
    monkeypatch.setenv("AIVF_ENV", "test")
    monkeypatch.setenv("AIVF_DASHBOARD_TOKEN", "dashboard-test-token")
    monkeypatch.setenv("AIVF_DASHBOARD_ROLE", role)
    monkeypatch.delenv("AIVF_DASHBOARD_USERS", raising=False)
    if users_json is not None:
        monkeypatch.setenv("AIVF_DASHBOARD_USERS", users_json)
    monkeypatch.setenv("AIVF_ADMIN_PANEL_CODE", "unit-test-admin-code-6bf041")
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
    if login_token is not None:
        response = client.post(
            "/login",
            data={"token": login_token},
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
        json={"code": "unit-test-admin-code-6bf041"},
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


def test_admin_unlock_fails_closed_when_private_code_is_unset(monkeypatch, tmp_path):
    client, _app_module = _make_client(monkeypatch, tmp_path, role="admin")
    monkeypatch.delenv("AIVF_ADMIN_PANEL_CODE")

    response = client.post(
        "/api/admin/unlock",
        json={"code": "unit-test-admin-code-6bf041"},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 503
    assert "AIVF_ADMIN_PANEL_CODE" in response.get_json()["error"]
    assert client.get("/api/admin/diagnostics").status_code == 403


def test_admin_unlock_code_does_not_grant_viewer_role_admin_access(monkeypatch, tmp_path):
    client, _app_module = _make_client(monkeypatch, tmp_path, role="viewer")

    response = client.post(
        "/api/admin/unlock",
        json={"code": "unit-test-admin-code-6bf041"},
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
        json={"code": "unit-test-admin-code-6bf041"},
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
        "model_api_key=supersecret Bearer token-value Authorization: hidden "
        "diarization_token=diarization-value password=pass-value cookie=session-value"
    )
    for secret in (
        "supersecret",
        "token-value",
        "hidden",
        "diarization-value",
        "pass-value",
        "session-value",
    ):
        assert secret not in text
    assert "[REDACTED]" in text


def test_dashboard_pages_render_for_authenticated_users(monkeypatch, tmp_path):
    client, _app_module = _make_client(monkeypatch, tmp_path, role="admin")
    for path in ("/", "/jobs", "/library", "/settings"):
        response = client.get(path)
        assert response.status_code == 200, path
    html = client.get("/jobs").get_data(as_text=True)
    assert 'id="jobsTable"' in html
    assert 'href="/library"' in html



def test_admin_operations_log_combines_sources_and_redacts_secrets(monkeypatch, tmp_path):
    client, app_module = _make_client(monkeypatch, tmp_path, role="admin")
    assert client.get("/api/admin/logs").status_code == 403

    unlock = client.post(
        "/api/admin/unlock",
        json={"code": "unit-test-admin-code-6bf041"},
        headers={"Origin": "http://localhost"},
    )
    assert unlock.status_code == 200

    app_module.db_insert_job("ops-log-job", "Render diagnostics demo", {})
    app_module.db_append_log(
        "ops-log-job",
        "ERROR",
        "Render failed because model_api_key=do-not-leak-this-value",
    )
    with app_module.get_db() as connection:
        connection.execute(
            "INSERT INTO job_events(job_id,event,details) VALUES (?,?,?)",
            ("ops-log-job", "job_interrupted", '{"message":"Worker stopped unexpectedly"}'),
        )

    from dashboard_store import DashboardStore
    DashboardStore(app_module.DB_PATH).record_audit_event(
        principal="user:admin-test",
        action="SETTINGS_CHANGED",
        resource="settings",
        resource_id="default_target_seconds",
        result="success",
        metadata={"test": "admin activity feed"},
    )

    response = client.get("/api/admin/logs?limit=120")
    assert response.status_code == 200
    payload = response.get_json()
    logs = payload["logs"]
    assert payload["counts"]["total"] == len(logs)
    assert any(entry["source"] == "job-log" and entry["job_id"] == "ops-log-job" for entry in logs)
    assert any(entry["source"] == "lifecycle" and entry["action"] == "job_interrupted" for entry in logs)
    assert any(entry["source"] == "audit" and entry["action"] == "SETTINGS_CHANGED" for entry in logs)
    serialized = json.dumps(payload)
    assert "do-not-leak-this-value" not in serialized
    assert "[REDACTED]" in serialized


def test_admin_operations_log_rejects_unauthorized_roles_and_bad_limits(monkeypatch, tmp_path):
    viewer, _app_module = _make_client(monkeypatch, tmp_path, role="viewer")
    assert viewer.get("/api/admin/logs").status_code == 403

    admin, _app_module = _make_client(monkeypatch, tmp_path, role="admin")
    unlocked = admin.post(
        "/api/admin/unlock",
        json={"code": "unit-test-admin-code-6bf041"},
        headers={"Origin": "http://localhost"},
    )
    assert unlocked.status_code == 200
    assert admin.get("/api/admin/logs?limit=not-a-number").status_code == 400


def test_admin_diagnostics_include_telemetry_without_secrets(monkeypatch, tmp_path):
    client, _app_module = _make_client(monkeypatch, tmp_path, role="admin")
    unlock = client.post(
        "/api/admin/unlock",
        json={"code": "unit-test-admin-code-6bf041"},
        headers={"Origin": "http://localhost"},
    )
    assert unlock.status_code == 200

    response = client.get("/api/admin/diagnostics")
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload["runtime"]["uptime_seconds"], int)
    assert "memory_rss_mb" in payload["runtime"]
    assert "http_requests_total" in payload["telemetry"]
    assert "http_5xx_total" in payload["telemetry"]
    assert "request_p95_ms" in payload["telemetry"]
    assert "dashboard-test-token" not in json.dumps(payload)


def test_multi_user_mode_disables_shared_token_and_preserves_role_checks(monkeypatch, tmp_path):
    users = json.dumps({
        "owner": {"token": "owner-user-token-distinct", "role": "admin"},
        "friend": {"token": "friend-user-token-distinct", "role": "viewer"},
        "unclassified": {"token": "unclassified-user-token"},
    })
    owner, app_module = _make_client(
        monkeypatch,
        tmp_path,
        users_json=users,
        login_token="owner-user-token-distinct",
    )

    # The configured owner can still sign in by their named account.
    assert owner.get("/").status_code == 200

    # The legacy shared admin token must no longer work once named users exist.
    shared = app_module.app.test_client()
    denied = shared.post(
        "/login",
        data={"token": "dashboard-test-token"},
        headers={"Origin": "http://localhost"},
    )
    assert denied.status_code == 401

    # A friend with a viewer token still cannot unlock or reach the admin panel.
    friend = app_module.app.test_client()
    assert friend.post(
        "/login",
        data={"token": "friend-user-token-distinct"},
        headers={"Origin": "http://localhost"},
    ).status_code == 302
    assert friend.post(
        "/api/admin/unlock",
        json={"code": "unit-test-admin-code-6bf041"},
        headers={"Origin": "http://localhost"},
    ).status_code == 403
    assert friend.get("/api/admin/diagnostics").status_code == 403

    # An omitted role is least-privilege in multi-user mode, not admin by default.
    unclassified = app_module.app.test_client()
    assert unclassified.post(
        "/login",
        data={"token": "unclassified-user-token"},
        headers={"Origin": "http://localhost"},
    ).status_code == 302
    with unclassified.session_transaction() as state:
        assert state["aivf_role"] == "viewer"
    assert unclassified.post(
        "/api/admin/unlock",
        json={"code": "unit-test-admin-code-6bf041"},
        headers={"Origin": "http://localhost"},
    ).status_code == 403


def test_invalid_multi_user_configuration_fails_closed(monkeypatch, tmp_path):
    client, _app_module = _make_client(
        monkeypatch,
        tmp_path,
        users_json="{not-valid-json",
        login_token=None,
    )
    response = client.post(
        "/login",
        data={"token": "dashboard-test-token"},
        headers={"Origin": "http://localhost"},
    )
    assert response.status_code == 401
