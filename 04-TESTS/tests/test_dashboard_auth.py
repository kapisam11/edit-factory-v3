import importlib


def _load(monkeypatch):
    monkeypatch.setenv("AIVF_DASHBOARD_TOKEN", "test-token")
    import dashboard_auth
    importlib.reload(dashboard_auth)
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.get("/")
    def home():
        return "ok"

    @app.post("/state-change")
    def state_change():
        return "changed"

    dashboard_auth.configure_dashboard_auth(app)
    return app


def _login(client):
    response = client.post("/login", data={"token": "test-token"})
    assert response.status_code == 302


def test_dashboard_requires_authentication(monkeypatch):
    app = _load(monkeypatch)
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_dashboard_token_logs_user_in(monkeypatch):
    app = _load(monkeypatch)
    client = app.test_client()
    _login(client)
    assert client.get("/").status_code == 200


def test_wrong_dashboard_token_is_rejected(monkeypatch):
    app = _load(monkeypatch)
    client = app.test_client()
    response = client.post("/login", data={"token": "wrong"})
    assert response.status_code == 401


def test_authenticated_state_change_requires_origin_signal(monkeypatch):
    app = _load(monkeypatch)
    client = app.test_client()
    _login(client)

    response = client.post("/state-change")
    assert response.status_code == 403


def test_authenticated_state_change_rejects_cross_origin(monkeypatch):
    app = _load(monkeypatch)
    client = app.test_client()
    _login(client)

    response = client.post("/state-change", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_authenticated_state_change_accepts_same_origin(monkeypatch):
    app = _load(monkeypatch)
    client = app.test_client()
    _login(client)

    response = client.post("/state-change", headers={"Origin": "http://localhost"})
    assert response.status_code == 200
