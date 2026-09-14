import importlib


def _load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIVF_OUTPUT_DIR", str(tmp_path / "output"))
    import web_app_v2
    importlib.reload(web_app_v2)
    web_app_v2.app.config["TESTING"] = True
    return web_app_v2.app


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
