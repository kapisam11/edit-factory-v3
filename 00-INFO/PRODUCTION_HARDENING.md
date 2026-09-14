# Production Hardening Status

This branch is the final verification pass for the dashboard and local single-host deployment.

## Implemented

- SQLite WAL and busy timeout.
- Append-only job logs instead of read/modify/write JSON logs.
- Startup reconciliation marks interrupted in-flight jobs explicitly.
- Spawned per-job processes with explicit lifecycle tracking.
- Running jobs can be terminated instead of pretending that `Future.cancel()` stopped them.
- Cancellation uses an atomic database state transition and cannot overwrite a terminal job.
- Cancelled jobs are terminal and SSE streams close for all terminal states.
- Job secrets are kept out of SQLite job records, logs, and API responses.
- Dashboard authentication, fail-closed same-origin protection for browser mutations, and security headers.
- Dashboard uploads use `secure_filename`, UUID storage names, extension allowlists, size limits, and ffprobe validation.
- Topic text cannot control package filesystem paths.
- Package resolution is contained inside the configured output root.
- Nested configuration dataclasses are reconstructed correctly on JSON load.
- Config persistence redacts API credentials.
- Model-provider credentials never fall through from Groq to OpenAI.
- Template FFmpeg overlays use argv-based subprocess execution with a hard timeout; filenames are not passed through a shell.
- Dashboard workflow selection is applied in the spawned worker, with canonical target-duration validation.
- Production Gunicorn uses one `gthread` worker with four threads so the long-lived dashboard SSE endpoint cannot block all other HTTP requests.
- Docker contains the complete production WSGI dependency boundary and does not rely on the old `worker.py` name.
- Persistent SQLite state is stored under `/app/state`.
- CI validates Python 3.9–3.12, Windows-sensitive modules, dependency audit, CLI smoke tests, and Docker image creation/imports.
- Runtime/generated directories are ignored going forward.
- Current-tree runtime FFmpeg/tool artifacts have been removed; historical Git objects remain a separately coordinated migration.

## Operational constraints

This project intentionally remains a single-host application. It does not require Redis,
Celery, Kubernetes, or microservices. Run one Gunicorn worker because active job-process
bookkeeping is intentionally process-local. The configured `gthread` threads share that one
process-local lifecycle state safely through the dashboard lifecycle lock.

## Final release gate

The remaining release gate is verification against the final commit itself: Python CI,
Docker build/runtime smoke, real FFmpeg integration, and automated lifecycle/security tests must
all pass. Manual production deployment on the target host remains an operator responsibility;
this document does not claim a deployment was performed when it was not.
