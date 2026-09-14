# Changelog

## Unreleased — final audit cleanup

### Reliability and security
- Removed the obsolete duplicate dashboard-operations module after consolidating cleanup and lifecycle handling in the supported dashboard path.
- Kept dashboard job execution behind the spawn-safe `dashboard_worker.py` process boundary.
- Retained real OS-level worker cancellation and terminal-state protection.
- Retained SQLite WAL, busy timeouts, append-only job logs, startup reconciliation, upload validation, path containment, and runtime-only API credentials.
- Kept authenticated dashboard access, same-origin protection for mutating browser requests, and security headers.

### Tooling and deployment
- Docker contains exactly the modules required by the production WSGI entrypoint.
- CI validates Python 3.9–3.12, Windows-sensitive modules, dependency security, CLI entry points, and the production Docker image.
- Local/generated state, media, models, and tool bundles remain excluded from source control.
- `aivf` points to the canonical `cli.py` entry point; `cli_v2.py` remains legacy compatibility code.

### Documentation
- Production architecture documentation now names one canonical CLI and one supported dashboard runtime path.
- Historical documents and code remain explicitly separated from the supported runtime; they are not imported or packaged as production modules.
- The remaining release gate is target-host deployment acceptance.

## 2.3.0 — 2026-09-03

### Reliability and security
- Moved dashboard job execution into the spawn-safe `dashboard_worker.py` process boundary.
- Added real OS-level worker cancellation instead of relying on `Future.cancel()`.
- Reconciled jobs that were left `running` after a dashboard restart as `interrupted`.
- Enabled SQLite WAL mode and busy timeouts for concurrent dashboard/worker access.
- Added timing-safe dashboard token comparison with `hmac.compare_digest`.
- Added video signature validation and optional `ffprobe` validation for uploads.
- Centralized workflow and target-duration validation for CLI and dashboard.
- Added graceful worker shutdown handling and single-web-worker deployment guidance.
- Added automatic output retention cleanup.
- Added security headers and production health reporting.

### Tooling and deployment
- CI and packaging support Python 3.9 through 3.12.
- Added Dockerfile, Docker Compose, Docker context exclusions, and Gunicorn configuration.
- Removed runtime FFmpeg/model bundles and generated output from the current repository tree.
- Tightened `.gitignore` for runtime state, generated media, local tools, and models.
- Added structured CLI logging, tracebacks, progress/ETA, and batch CSV/JSON support.

### Documentation
- Consolidated operational documentation under `00-INFO/`.
- Historical Git objects containing old binaries are not part of the supported source tree; target-host acceptance remains a separate release gate.
