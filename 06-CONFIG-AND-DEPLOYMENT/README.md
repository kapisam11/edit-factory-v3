# Config and deployment

This folder contains files used to build and deploy Edit Factory.

- `Dockerfile` — immutable production container image
- `docker-compose.yml` — production/host deployment definition; it requires an immutable `AIVF_IMAGE=...@sha256:...`
- `docker-compose.dev.yml` — local development definition; it builds from source and may use a local image
- `gunicorn.conf.py` — production web-server settings
- `aivf_config.json` — example/application configuration
- `requirements.txt` — dependency reference

The Python package definition and locked dependency graph live at repository root (`pyproject.toml` and `uv.lock`). The production Docker image uses `uv sync --frozen` so CI and container builds consume the same dependency lock.

## Production deployment

Production Compose intentionally has no build fallback and no `latest` default. Set an immutable image digest:

```bash
export AIVF_IMAGE=ghcr.io/kapisam11/edit-factory-v3@sha256:<64-hex-digest>
docker compose -f 06-CONFIG-AND-DEPLOYMENT/docker-compose.yml pull
docker compose -f 06-CONFIG-AND-DEPLOYMENT/docker-compose.yml up -d --no-build
```

Run the explicit database migration before first start and before any schema-changing release:

```bash
docker compose -f 06-CONFIG-AND-DEPLOYMENT/docker-compose.yml run --rm --no-deps web aivf-db-migrate /app/state/jobs.db
```

The deployment helper performs backup verification, restore drills, immutable-image rollout, health checks, smoke tests, and automatic rollback on rollout failure.

Production Compose defaults to secure session cookies and requires TLS/reverse-proxy termination when exposed to users. For local HTTP development, explicitly set `AIVF_COOKIE_SECURE=0` in the development environment only.

## Local development

Use the separate development definition so source builds never become an accidental production deployment:

```bash
docker compose -f 06-CONFIG-AND-DEPLOYMENT/docker-compose.dev.yml up --build -d
```

Never use the development Compose file on the production host.
