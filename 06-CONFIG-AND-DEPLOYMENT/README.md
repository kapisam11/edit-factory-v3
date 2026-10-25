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

## Windows production startup

`START-ALL.ps1` and `START-ALL.bat` intentionally start the source-built **development** stack. Do not use them for production. Use the separate `START-PRODUCTION.ps1` or `START-PRODUCTION.bat` path instead.

Before starting production, configure `06-CONFIG-AND-DEPLOYMENT/.env` with:

```text
AIVF_IMAGE=ghcr.io/kapisam11/edit-factory-v3@sha256:<64-hex-digest>
FLASK_SECRET_KEY=<generated secret of at least 32 characters>
AIVF_DASHBOARD_TOKEN=<generated token of at least 32 characters>
AIVF_ADMIN_PANEL_CODE=<private extra unlock code, not committed to Git>
```

The production launcher refuses mutable image tags, missing/placeholder secrets, invalid Compose configuration, and a different image from the one already configured in the production Compose project. The hidden admin console has no source-code fallback code: configure `AIVF_ADMIN_PANEL_CODE` in this ignored `.env` file. Use a private code; a longer code is safer than a four-digit PIN. If unset, the admin console stays locked and returns a configuration error. It does not build source. Production Compose forces `AIVF_ENV=production`, secure cookies, and `AIVF_ALLOW_RUNTIME_MIGRATIONS=0`, even if those variables in the shared `.env` contain development values.

On first setup, explicitly initialize and migrate the database, then start the pinned production image:

```powershell
.\START-PRODUCTION.ps1 -Migrate
```

If a database already exists, the migration mode creates and verifies a database-only backup at `/app/state/backups/pre-migration-<UTC timestamp>.tar.gz` before running `aivf-db-migrate`. This is a local pre-migration safeguard on the same Docker state volume, **not** a disaster-recovery or off-host backup. Configure and test the full backup/restore procedure separately.

For ordinary restarts of the same already-migrated image, run:

```powershell
.\START-PRODUCTION.ps1
```

Without `-Migrate`, the launcher verifies the existing schema without changing it and fails closed if the schema is missing or incompatible. The launcher deliberately refuses image-version changes; use the controlled backup/migration/rollback deployment procedure for releases rather than replacing a running image through a simple start command.

Autonomous publishing is not started by the default production launch. The `automation` Compose profile remains opt-in.

## Local development

Use the separate development definition so source builds never become an accidental production deployment:

```bash
docker compose -f 06-CONFIG-AND-DEPLOYMENT/docker-compose.dev.yml up --build -d
```

On Windows, `START-ALL.ps1` creates a private `AIVF_ADMIN_PANEL_CODE` in the ignored `06-CONFIG-AND-DEPLOYMENT/.env` file if needed. It deliberately does not print the unlock code, dashboard token, or resolved Compose environment; read the values from that private file locally when needed. Existing configured values are preserved. If you start Compose manually, set the variable in that private `.env` yourself. The source repository does not contain a default unlock code; if the variable is empty, the hidden admin console stays locked.

Never use the development Compose file on the production host.

## Resource policy

The production container has bounded CPU, PID, disk, and memory limits. The default media worker ceiling is 12 GiB per job inside a 16 GiB container, while the global reservation ceiling prevents multiple maximum-sized AI jobs from being admitted simultaneously. Tune `AIVF_MAX_JOB_MEMORY_MB`, `AIVF_MAX_RESERVED_MEMORY_MB`, `AIVF_CONTAINER_MEMORY`, and the other `AIVF_*` resource variables for the actual host rather than removing the limits.

## Off-host backup

For a deployment that requires off-host backups, configure the production environment with `AIVF_BACKUP_REMOTE`, `AIVF_BACKUP_REMOTE_DIR`, and `AIVF_BACKUP_ENCRYPTION_KEY`. The deployment helper encrypts the backup before upload and verifies the remote SHA-256 and HMAC sidecars. Set `AIVF_REQUIRE_OFFHOST_BACKUP=1` to make the rollout fail closed when off-host backup is not configured. The encryption key must be supplied through the host's secret environment/secret manager; never commit it or put it on a command line.
