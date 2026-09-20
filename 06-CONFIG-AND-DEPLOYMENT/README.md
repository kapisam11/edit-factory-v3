# Config and deployment

This folder contains files used to build and deploy Edit Factory.

- `Dockerfile` — production container image
- `docker-compose.yml` — local/host deployment definition
- `gunicorn.conf.py` — production web-server settings
- `aivf_config.json` — example/application configuration
- `requirements.txt` — dependency reference

The Python package definition and locked dependency graph live at repository root (`pyproject.toml` and `uv.lock`). The production Docker image uses `uv sync --frozen` so CI and container builds consume the same dependency lock.
\nProduction Compose defaults to secure session cookies and requires TLS/reverse-proxy termination when exposed to users. For local HTTP development, explicitly set `AIVF_COOKIE_SECURE=0` in the development environment only.\n