"""Production Gunicorn settings for the Edit Factory dashboard."""
# The application is installed from the root package configuration. Keep the
# working directory at the repository/deployment root so both source checkouts
# and installed wheels resolve the same canonical app.wsgi entrypoint.
chdir = "."
workers = 1
worker_class = "gthread"
threads = 4
bind = "0.0.0.0:5000"
timeout = 0
preload_app = False


def worker_exit(server, worker):
    """Terminate active job processes when the Gunicorn worker exits."""
    try:
        from dashboard_shutdown import shutdown_active_workers

        shutdown_active_workers()
    except Exception as exc:
        server.log.error("Failed to terminate active AIVF jobs on worker exit: %s", exc)
