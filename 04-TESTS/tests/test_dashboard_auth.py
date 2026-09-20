import importlib


def _load(monkeypatch):
    monkeypatch.setenv("AIVF_DASHBOARD_TOKEN", "test-token")
    monkeypatch.setenv("AIVF_COOKIE_SECURE", "0")
    import dashboard_auth
    importlib.reload(dashboard_auth)
    dashboard_auth._login_attempts.clear()
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


def test_insecure_local_mode_is_loopback_only(monkeypatch):
    monkeypatch.delenv("AIVF_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setenv("AIVF_ALLOW_INSECURE_LOCAL", "1")
    import dashboard_auth
    importlib.reload(dashboard_auth)
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.get("/")
    def home():
        return "ok"

    dashboard_auth.configure_dashboard_auth(app)
    client = app.test_client()
    assert client.post("/login", data={"token": "local-development"}).status_code == 302
    client2 = app.test_client()
    response = client2.post(
        "/login",
        data={"token": "local-development"},
        base_url="https://example.test",
        headers={"X-Forwarded-For": "127.0.0.1"},
    )
    assert response.status_code == 401


def test_login_rate_limit_blocks_excessive_attempts(monkeypatch):
    app = _load(monkeypatch)
    import dashboard_auth
    dashboard_auth._login_attempts.clear()
    client = app.test_client()
    for _ in range(10):
        assert client.post("/login", data={"token": "wrong"}).status_code == 401
    assert client.post("/login", data={"token": "wrong"}).status_code == 429


def test_session_cookie_is_secure(monkeypatch):
    app = _load(monkeypatch)
    app.config["SESSION_COOKIE_SECURE"] = True
    client = app.test_client()
    import dashboard_auth
    dashboard_auth._login_attempts.clear()
    _login(client)
    response = client.get("/")
    cookie = response.headers.get("Set-Cookie", "")
    assert "Secure" in cookie


def test_dashboard_emits_nonce_based_csp(monkeypatch):
    app = _load(monkeypatch)
    client = app.test_client()
    _login(client)
    response = client.get("/")
    policy = response.headers["Content-Security-Policy"]
    assert "script-src 'self';" not in policy
    assert "script-src 'self' 'nonce-" in policy
    assert "'unsafe-inline'" not in policy.split("script-src", 1)[1].split(";", 1)[0]
