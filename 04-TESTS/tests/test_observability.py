import importlib


def _load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIVF_OUTPUT_DIR", str(tmp_path / "output"))
    import web_app_v3
    importlib.reload(web_app_v3)
    web_app_v3.app.config["TESTING"] = True
    return web_app_v3.app


def test_production_observability_adds_request_id_and_health(monkeypatch, tmp_path):
    app = _load_app(monkeypatch, tmp_path)
    from app.observability import install_observability

    install_observability(app)
    client = app.test_client()

    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["request_id"] == response.headers["X-Request-ID"]


def test_observability_preserves_valid_request_id_and_rejects_malformed_id(monkeypatch, tmp_path):
    app = _load_app(monkeypatch, tmp_path)
    from app.observability import install_observability

    install_observability(app)

    @app.get("/ok-request-id")
    def ok_request_id():
        return {"status": "ok"}

    client = app.test_client()
    valid = "req-test-2026"
    response = client.get("/ok-request-id", headers={"X-Request-ID": valid})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == valid

    malformed = "bad id with spaces"
    response = client.get("/ok-request-id", headers={"X-Request-ID": malformed})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != malformed


def test_readyz_reports_tool_and_directory_readiness(monkeypatch, tmp_path):
    app = _load_app(monkeypatch, tmp_path)
    from app.observability import install_observability

    install_observability(app)
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    response = app.test_client().get("/readyz")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ready"
    assert payload["checks"]
    assert all(set(item) == {"key", "ok"} for item in payload["checks"])
    assert all("detail" not in item for item in payload["checks"])


def test_readyz_fails_when_disk_below_configured_threshold(monkeypatch, tmp_path):
    app = _load_app(monkeypatch, tmp_path)
    from app.observability import install_observability
    install_observability(app)

    monkeypatch.setenv("AIVF_MIN_FREE_DISK_MB", "999999999")
    response = app.test_client().get("/readyz")
    assert response.status_code == 503
    assert response.get_json()["status"] == "not_ready"
    assert set(response.get_json()) == {"status", "request_id"}
