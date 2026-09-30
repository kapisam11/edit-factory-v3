"""Authentication and request-hardening for the single-user dashboard."""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from urllib.parse import urlparse

from flask import abort, g, redirect, render_template, request, session, url_for
from ai_video_factory.authorization import Role, normalize_role, role_allows

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATHS = {"/login", "/logout", "/api/health"}
_LOGIN_LIMIT = 10
_LOGIN_WINDOW_SECONDS = 60.0
_login_attempts: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _configured_users(default_role: Role) -> dict[str, dict[str, str]]:
    raw = os.environ.get("AIVF_DASHBOARD_USERS", "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    users: dict[str, dict[str, str]] = {}
    for user_id, item in payload.items():
        if not isinstance(item, dict):
            continue
        token_value = str(item.get("token") or "").strip()
        if not token_value:
            continue
        try:
            role_value = normalize_role(item.get("role") or default_role.value).value
        except ValueError:
            continue
        users[str(user_id).strip() or "user"] = {"token": token_value, "role": role_value}
    return users


def _loopback_request() -> bool:
    remote = (request.remote_addr or "").strip().lower()
    host = request.host.split(":", 1)[0].strip("[]").lower()
    return remote in {"127.0.0.1", "::1"} and host in {"localhost", "127.0.0.1", "::1"}


def _login_allowed(client: str) -> bool:
    now = time.monotonic()
    with _login_lock:
        recent = [ts for ts in _login_attempts.get(client, []) if now - ts < _LOGIN_WINDOW_SECONDS]
        if len(recent) >= _LOGIN_LIMIT:
            _login_attempts[client] = recent
            return False
        recent.append(now)
        _login_attempts[client] = recent
        if len(_login_attempts) > 1024:
            cutoff = now - _LOGIN_WINDOW_SECONDS
            for key in [
                key for key, timestamps in _login_attempts.items()
                if key != client and not any(ts >= cutoff for ts in timestamps)
            ]:
                _login_attempts.pop(key, None)
            while len(_login_attempts) > 1024:
                evictable = [
                    (key, max(timestamps))
                    for key, timestamps in _login_attempts.items()
                    if key != client and timestamps
                ]
                if not evictable:
                    break
                oldest_client = min(evictable, key=lambda item: item[1])[0]
                _login_attempts.pop(oldest_client, None)
        return True



def _same_origin_request() -> bool:
    """Require an explicit browser origin signal for authenticated state changes."""
    expected = request.host_url.rstrip("/")
    origin = request.headers.get("Origin")
    if origin:
        return origin.rstrip("/") == expected

    referer = request.headers.get("Referer")
    if referer:
        parsed = urlparse(referer)
        if not parsed.scheme or not parsed.netloc:
            return False
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/") == expected

    # A browser state-changing request without either header is ambiguous and
    # must not be accepted as same-origin merely because authentication exists.
    return False


def configure_dashboard_auth(app):
    if app.config.get("_AIVF_AUTH_CONFIGURED"):
        return
    app.config["_AIVF_AUTH_CONFIGURED"] = True
    token = os.environ.get("AIVF_DASHBOARD_TOKEN", "").strip()
    allow_insecure_local = os.environ.get("AIVF_ALLOW_INSECURE_LOCAL", "0") == "1"
    configured_role = normalize_role(os.environ.get("AIVF_DASHBOARD_ROLE", "admin"))

    # Flask sessions need a stable signing key. Prefer an explicit deployment secret;
    # derive a deterministic fallback from the dashboard token so multiple workers
    # share the same session key without duplicating the credential in configuration.
    session_key = os.environ.get("AIVF_DASHBOARD_SECRET_KEY", "").strip()
    if not session_key:
        if token:
            session_key = hashlib.sha256(
                ("AIVF-DASHBOARD-SESSION:" + token).encode("utf-8")
            ).hexdigest()
        elif allow_insecure_local:
            session_key = hashlib.sha256(
                b"AIVF-DASHBOARD-SESSION:local-development"
            ).hexdigest()
        else:
            session_key = secrets.token_hex(32)
    app.secret_key = session_key

    configured_users = _configured_users(configured_role)
    default_user = os.environ.get("AIVF_DASHBOARD_USER", "local-user").strip() or "local-user"
    if not token and not allow_insecure_local:
        app.logger.warning("AIVF_DASHBOARD_TOKEN is unset; dashboard access will fail closed")

    cookie_secure_default = "0" if allow_insecure_local else "1"
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.environ.get("AIVF_COOKIE_SECURE", cookie_secure_default) == "1",
    )

    @app.before_request
    def assign_csp_nonce():
        g.aivf_csp_nonce = secrets.token_urlsafe(18)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'nonce-%s'; script-src-attr 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        ) % g.aivf_csp_nonce
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.before_request
    def dashboard_authentication():
        path = request.path
        if path.startswith("/static/") or path in PUBLIC_PATHS:
            return None
        if session.get("aivf_authenticated"):
            if request.method not in SAFE_METHODS and not _same_origin_request():
                abort(403)
            return None
        if path == "/":
            return redirect(url_for("dashboard_login"))
        return ("Authentication required", 401)

    @app.route("/login", methods=["GET", "POST"])
    def dashboard_login():
        if request.method == "GET":
            return render_template("login.html")
        client = request.remote_addr or "unknown"
        if not _login_allowed(client):
            return "Too many login attempts. Try again later.", 429
        supplied = request.form.get("token", "")
        matched_user = None
        matched_role = configured_role
        if configured_users:
            for user_id, user_config in configured_users.items():
                candidate = user_config["token"]
                if hmac.compare_digest(
                    hashlib.sha256(supplied.encode()).digest(),
                    hashlib.sha256(candidate.encode()).digest(),
                ):
                    matched_user = user_id
                    matched_role = normalize_role(user_config["role"])
                    break
        valid = bool(token) and hmac.compare_digest(
            hashlib.sha256(supplied.encode()).digest(), hashlib.sha256(token.encode()).digest()
        )
        local_dev_valid = allow_insecure_local and supplied == "local-development" and _loopback_request()
        if valid or local_dev_valid or matched_user:
            session.clear()
            session["aivf_authenticated"] = True
            session["aivf_role"] = matched_role.value
            session["aivf_user_id"] = matched_user or default_user
            return redirect("/")
        return "Invalid dashboard token", 401

    @app.context_processor
    def dashboard_auth_context() -> dict:
        raw_role = session.get("aivf_role") or configured_role.value
        try:
            role = normalize_role(raw_role)
        except ValueError:
            role = Role.VIEWER
        return {
            "aivf_role": role.value,
            "aivf_user_id": str(session.get("aivf_user_id") or default_user),
        }

    def require_dashboard_role(required: str | Role) -> None:
        raw_role = session.get("aivf_role") or configured_role.value
        if not role_allows(normalize_role(raw_role), required):
            abort(403)

    app.extensions["aivf_require_role"] = require_dashboard_role

    @app.post("/logout")
    def dashboard_logout():
        session.clear()
        return redirect(url_for("dashboard_login"))
