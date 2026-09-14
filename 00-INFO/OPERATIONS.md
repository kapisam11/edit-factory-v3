# Operations

## Local development

Install from `pyproject.toml` with the extras required for the features you use. The supported
Python floor is 3.9. FFmpeg and optional model/OCR assets are runtime dependencies; generated
media, uploads, knowledge data, SQLite state, and local tool bundles are not source artifacts.

## Dashboard authentication

Production dashboard access requires `AIVF_DASHBOARD_TOKEN`. Set a strong `FLASK_SECRET_KEY` as
well. For HTTPS, set `AIVF_COOKIE_SECURE=1`. `AIVF_ALLOW_INSECURE_LOCAL=1` exists only for explicit
local development and must not be used for internet-facing deployments.

## Dashboard execution

Run exactly one Gunicorn worker with the supplied configuration. Jobs are tracked durably in
SQLite and executed in dedicated spawned processes. SQLite uses WAL mode and a 10-second busy
timeout. API credentials are supplied in memory and are never persisted in job records or logs.

## Recovery and cancellation

A process restart marks outstanding `queued`, `running`, and `cancelling` jobs as `interrupted`.
Running cancellation terminates the dedicated job process and then records `cancelled`. SSE log
streams close for all terminal states.

## Retention

Use the administrative cleanup endpoint after authentication to remove packages older than the
configured retention window. Cleanup must never be used as a substitute for backups. Keep output
and state volumes persistent in production.

## Docker

Use `docker compose up --build`. The container expects `FLASK_SECRET_KEY` and
`AIVF_DASHBOARD_TOKEN` to be supplied through the environment. Named volumes persist output,
uploads, knowledge data, and `/app/state/jobs.db` across container replacement.

## Release gate

Before a production release, CI must pass Python tests, lint, dependency audit, CLI smoke tests,
and the Docker build. A release should also include a real end-to-end render and cancellation
smoke test on the target operating system.
