"""Authentication and request-hardening for the single-user dashboard."""
import hashlib
import hmac
import os
from urllib.parse import urlparse

from flask import abort, redirect, render_template, request, session, url_for

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATHS = {"/login", "/logout", "/api/health"}


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
    if not token and not allow_insecure_local:
        app.logger.warning("AIVF_DASHBOARD_TOKEN is unset; dashboard access will fail closed")

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_SECURE=os.environ.get("AIVF_COOKIE_SECURE", "0") == "1",
    )

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
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
        supplied = request.form.get("token", "")
        valid = bool(token) and hmac.compare_digest(
            hashlib.sha256(supplied.encode()).digest(), hashlib.sha256(token.encode()).digest()
        )
        if valid or (allow_insecure_local and supplied == "local-development"):
            session.clear()
            session["aivf_authenticated"] = True
            return redirect("/")
        return "Invalid dashboard token", 401

    @app.post("/logout")
    def dashboard_logout():
        session.clear()
        return redirect(url_for("dashboard_login"))
