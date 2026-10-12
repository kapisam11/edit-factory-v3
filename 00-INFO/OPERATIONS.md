# Operations

## Local development

Install from the repository-root `pyproject.toml` with the extras required for the features you use. The package metadata supports Python 3.10 and newer, while the Windows `START-ALL.ps1` launcher and pinned production Docker image use Python 3.12. FFmpeg and optional model/OCR assets are runtime dependencies; generated media, uploads, knowledge data, SQLite state, and local tool bundles are not source artifacts.

The repository contains a committed `uv.lock`. The Windows launcher and CI use `uv sync --frozen` (the launcher selects Python 3.12), and the production Docker image installs from that same lock. Optional features remain separate extras; install only the extras needed by the workflow.

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

The Windows launcher uses `06-CONFIG-AND-DEPLOYMENT/docker-compose.dev.yml` and binds the dashboard to `127.0.0.1`. It creates strong local secrets in the ignored `06-CONFIG-AND-DEPLOYMENT/.env` file and preserves them across restarts. Starting Compose manually without configured secrets now fails instead of using placeholder credentials.

The development defaults are four container CPUs, 8 GiB memory, 512 PIDs, and one concurrent job. Tune `AIVF_CONTAINER_CPUS`, `AIVF_CONTAINER_MEMORY`, `AIVF_CONTAINER_PIDS`, and `AIVF_MAX_CONCURRENT_JOBS` in the local environment file; keep enough memory available for Windows and other applications. Job stage timings record the selected resource limits and elapsed time. A final V3 render follows the selected platform profile's 30 FPS contract and is fully decoded before it can be marked valid.

Named volumes persist output, uploads, knowledge data, and `/app/state/jobs.db` across container replacement. Persistence is not a backup.

## Local backup and recovery

For the Windows Docker setup, create a folder outside the application data and keep backup files on a different physical drive when possible. From PowerShell at the repository root:

```powershell
New-Item -ItemType Directory -Force .\backups | Out-Null
$BackupHostPath = (Resolve-Path .\backups).Path
$ComposeArgs = @("--env-file", "06-CONFIG-AND-DEPLOYMENT/.env", "-f", "06-CONFIG-AND-DEPLOYMENT/docker-compose.dev.yml")
$BackupName = "edit-factory-$((Get-Date).ToString('yyyyMMdd-HHmmss')).tar.gz"

docker compose @ComposeArgs run --rm --no-deps -v "${BackupHostPath}:/backups" web aivf-backup backup --db /app/state/jobs.db --backup "/backups/$BackupName" --knowledge /app/knowledge_base_v3 --output /app/output
docker compose @ComposeArgs run --rm --no-deps -v "${BackupHostPath}:/backups" web aivf-backup verify --backup "/backups/$BackupName"
```

A backup includes the SQLite job database, knowledge data, completed output files, and a manifest with integrity hashes. Periodically test a restore into a new, empty directory; do not restore over a live database or output directory. Example restore drill:

```powershell
$RestoreDir = "/app/state/restore-check-$((Get-Date).ToString('yyyyMMdd-HHmmss'))"
docker compose @ComposeArgs run --rm --no-deps -v "${BackupHostPath}:/backups" web aivf-backup restore --backup "/backups/$BackupName" --destination $RestoreDir
```

The restore command validates the archive before writing a separate restored copy. To recover the active installation, stop the dashboard first, restore into an isolated directory, and only then copy its `jobs.db`, `output`, and `knowledge_base_v3` contents into their corresponding volume paths. Verify the restored videos and restart the stack after the copy. Keep at least one verified backup outside the PC; a backup on the same disk does not protect against disk failure.

## Release gate

Before a production release, CI must pass Python tests, lint, dependency audit, CLI smoke tests,
and the Docker build. A release should also include a real end-to-end render, cancellation/restart
smoke tests, and human visual acceptance on the target operating system.

## V3 contract and job-state guarantees

V3 persists a typed, immutable blueprint and reads it back through `V3Blueprint.from_dict()` before rendering. The renderer consumes a derived `V3RenderPlan`; creative planning does not call FFmpeg.

Job lifecycle transitions are enforced by the application storage boundary and SQLite trigger. Worker startup claims a queued job atomically, and a job only reaches `done` after its final artifact passes media validation. `error` and `interrupted` jobs can return to `queued` only through the bounded retry policy.

Runtime configuration for timeouts, queue limits, retry limits, upload limits, disk thresholds, and cleanup windows is exposed through the typed `RuntimeConfig` boundary. Optional capabilities are reported independently from credentials.

Transient media files are cleaned on V3 startup/failure. Final artifacts are promoted only after render validation; known temporary names use atomic replacement where supported.
