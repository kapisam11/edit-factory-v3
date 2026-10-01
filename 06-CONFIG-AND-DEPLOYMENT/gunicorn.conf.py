"""Production Gunicorn settings for the Edit Factory dashboard."""
import os
# The application is installed from the root package configuration. Keep the
# working directory at the repository/deployment root so both source checkouts
# and installed wheels resolve the same canonical app.wsgi entrypoint.
chdir = "."
workers = int(os.environ.get("AIVF_GUNICORN_WORKERS", "1"))
if workers != 1:
    raise RuntimeError(
        "AIVF_GUNICORN_WORKERS must remain 1 until job ownership/runtime state is externalized "
        "for multi-process web serving"
    )
worker_class = "gthread"
threads = int(os.environ.get("AIVF_GUNICORN_THREADS", "4"))
if not 2 <= threads <= 16:
    raise RuntimeError("AIVF_GUNICORN_THREADS must be between 2 and 16")
bind = "0.0.0.0:5000"
timeout = 960  # Finite request lifetime; exceeds the 900s SSE application cap with margin.
preload_app = False


def worker_exit(server, worker):
    """Terminate active job processes when the Gunicorn worker exits."""
    try:
        from dashboard_shutdown import shutdown_active_workers

        shutdown_active_workers()
    except Exception as exc:
        server.log.error("Failed to terminate active AIVF jobs on worker exit: %s", exc)
