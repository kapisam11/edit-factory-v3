# Config and deployment

This folder contains files used to build and deploy Edit Factory.

- `Dockerfile` — production container image
- `docker-compose.yml` — local/host deployment definition
- `gunicorn.conf.py` — production web-server settings
- `aivf_config.json` — example/application configuration
- `requirements.txt` — dependency reference

The Python package definition and lockfile stay beside the application in `01-MAIN-CODE/` so `pip install -e ./01-MAIN-CODE` and `uv lock` work without special path tricks.
