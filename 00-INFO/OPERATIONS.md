# Operations

## Local development

Use **Python 3.12** as the production runtime for the Windows target and the Docker image. The package supports Python 3.10+, and CI checks 3.10–3.14, but do not treat the compatibility matrix as a substitute for selecting one known runtime. From a checkout, install with `uv sync --frozen --python 3.12 --extra web --extra dev`. Keep `uv.lock` in sync with dependency changes. FFmpeg and optional model/OCR assets are runtime dependencies; generated media, uploads, knowledge data, SQLite state, and local tool bundles are runtime data.

## Dashboard authentication

Production dashboard access requires `AIVF_DASHBOARD_TOKEN`. Set a strong `FLASK_SECRET_KEY` as
well. For HTTPS, set `AIVF_COOKIE_SECURE=1`. `AIVF_ALLOW_INSECURE_LOCAL=1` exists only for explicit
local development and must not be used for internet-facing deployments.

## Dashboard execution

Run exactly one Gunicorn worker with the supplied configuration. Jobs are tracked durably in
SQLite and executed in dedicated spawned processes. SQLite uses WAL mode and a 10-second busy
timeout. API credentials are supplied in memory and are never persisted in job records or logs.

## Recovery and cancellation

A process restart marks outstanding `queued`, `running`, and `cancelling` jobs as `interrupted`. Resource governance also enforces principal queue limits, total/per-job storage limits, a render wall-clock budget, a cross-process FFmpeg concurrency budget, and bounded SSE connections/lifetime. Terminal job/log records and stale upload files are periodically expired.
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

## Windows backup and restore

For the Windows Docker Compose development stack, run `.\\BACKUP-EDIT-FACTORY.ps1 -Destination "E:\\EditFactoryBackups"` after choosing a destination on a different trusted NTFS drive. The script builds the backup utility without restarting the running dashboard, backs up the main job database, auxiliary non-secret state databases, knowledge, output and uploads, copies the archive to the destination drive, and verifies the copy. Provider token/secret files are deliberately excluded and must be reauthorized after recovery. The local archive is **not encrypted**; it is ACL-restricted, so do not store it on a shared or untrusted drive. Use the encrypted off-host helper for off-host copies.

Test restoration into a new, empty directory and never over the live application data:

```powershell
uv sync --frozen --python 3.12 --extra web --extra dev
uv run aivf-backup restore --backup "E:\\EditFactoryBackups\\edit-factory-v3-<timestamp>.tar.gz" --destination "E:\\EditFactoryRestoreTest"
```

Replace the example filename with the archive produced by the script. Inspect the restored `jobs.db`, auxiliary `state/*.sqlite` databases, output, uploads, and knowledge files. Restore validation checks manifest hashes and every SQLite database before extraction.

## Release gate

Before a production release, CI must pass Python tests, lint, dependency audit, CLI smoke tests, and the Docker build. For the personal Windows target, follow [WINDOWS-11-PRODUCTION-ACCEPTANCE.md](WINDOWS-11-PRODUCTION-ACCEPTANCE.md): run the full launcher checks, render one representative 60-second video, review the final MP4 manually, test a failure/restart, and make and restore a verified backup.

## V3 contract and job-state guarantees

V3 persists a typed, immutable blueprint and reads it back through `V3Blueprint.from_dict()` before rendering. The renderer consumes a derived `V3RenderPlan`; creative planning does not call FFmpeg.

Job lifecycle transitions are enforced by the application storage boundary and SQLite trigger. Worker startup claims a queued job atomically, and a job only reaches `done` after its final artifact passes media validation. `error` and `interrupted` jobs can return to `queued` only through the bounded retry policy.

Runtime configuration for timeouts, queue limits, retry limits, upload limits, disk thresholds, and cleanup windows is exposed through the typed `RuntimeConfig` boundary. Optional capabilities are reported independently from credentials.

Transient media files are cleaned on V3 startup/failure. Final artifacts are promoted only after render validation; known temporary names use atomic replacement where supported.
