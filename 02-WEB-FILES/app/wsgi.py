"""Production WSGI entrypoint for Gunicorn or another WSGI server."""
import os
import sys

from app import web_app_v2 as _web_app_v2
from werkzeug.middleware.proxy_fix import ProxyFix

# Production must use a stable explicitly configured session secret. The
# application module historically generated a random fallback at import time;
# overriding it here prevents session invalidation and multi-worker mismatch.
flask_secret_key = os.environ.get("FLASK_SECRET_KEY", "").strip()
if not flask_secret_key:
    raise RuntimeError(
        "FLASK_SECRET_KEY must be set for the production dashboard; refusing to use a per-process random key."
    )
_web_app_v2.app.config["SECRET_KEY"] = flask_secret_key

# Legacy modules import web_app_v2; point that name at the canonical implementation.
sys.modules.setdefault("web_app_v2", _web_app_v2)

app = _web_app_v2.app

if os.environ.get("AIVF_TRUST_PROXY", "0") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)

from dashboard_auth import configure_dashboard_auth
from dashboard_compat import register_dashboard_compat
from app.observability import install_observability

configure_dashboard_auth(app)
register_dashboard_compat(app)
install_observability(app)


@app.before_request
def prune_runtime_secrets():
    """Drop runtime job secrets once their jobs no longer need them.

    Secrets are deliberately kept in memory only. Pruning terminal or missing
    jobs on normal requests prevents the parent process dictionary from growing
    forever on long-running dashboard instances.
    """
    runtime_secrets = getattr(_web_app_v2, "_runtime_secrets", None)
    db_get_job = getattr(_web_app_v2, "db_get_job", None)
    terminal_statuses = getattr(_web_app_v2, "TERMINAL_STATUSES", set())
    if not isinstance(runtime_secrets, dict) or not callable(db_get_job):
        return

    for job_id in list(runtime_secrets):
        try:
            job = db_get_job(job_id)
        except Exception:
            continue
        if not job or job.get("status") in terminal_statuses:
            runtime_secrets.pop(job_id, None)


__all__ = ["app"]
