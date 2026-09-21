# Engineering and Operations

This document describes the production architecture actually used by Edit Factory v3. It replaces speculative architecture assumptions with the current repository design.

## Runtime architecture

The production dashboard is a Flask application backed by SQLite. Each active production is isolated into a spawned worker process. FFmpeg and FFprobe are invoked through hardened helpers in the media pipeline, and completed packages are validated before they are exposed.

```text
Browser
  |
  v
Flask dashboard
  |
  +--> SQLite jobs/settings/logs
  |
  +--> in-process queue + spawned worker per active job
  |       |
  |       +--> AI/content pipeline
  |       +--> FFmpeg / FFprobe
  |       +--> validated output package
  |
  +--> optional HybridCache
          |
          +--> local TTL cache
          +--> optional Redis backing store
```

The current design intentionally stays single-host. It already has a process boundary for heavy work; a distributed queue or microservice deployment is a future scaling step, not a requirement for normal operation.

## Database and caching

`01-MAIN-CODE/dashboard_store.py` is the reusable SQLite storage boundary. It centralizes the connection settings and bounded write retries while preserving the existing dashboard helper API.

The storage layer maintains indexes for the dashboard's actual query patterns:

- `jobs(status, created_at)` for queue/history queries.
- `jobs(status, updated_at)` for status-oriented maintenance.
- `job_logs(job_id, id)` for incremental log streaming.
- `rate_limits(client_ip, ts)` for request throttling.

The dashboard cache in `01-MAIN-CODE/dashboard_cache.py` is safe by default because it uses memory only. Setting `AIVF_REDIS_URL` enables Redis as shared cache backing when the optional `redis` Python package is installed. Cache failures fall back to memory and do not become a dashboard outage.

Only non-secret dashboard metadata is cached. Provider/API keys remain runtime-only and are never placed in cache payloads.

## UX and operational APIs

The dashboard includes authenticated queue monitoring and live logs. The hardening pass also provides:

- `GET /api/jobs/<job_id>/preview` for the preferred rendered video after completion.
- `POST /api/jobs/<job_id>/retry` to requeue a failed or interrupted job.
- `/api/health` for minimal unauthenticated liveness; authenticated `/api/health/details` for operational media-tool and capacity diagnostics.

The browser can use the preview endpoint as soon as the job reaches `done`.

## Security scanning

`.github/workflows/security.yml` runs:

- GitHub CodeQL against Python on pushes, pull requests, and a weekly schedule.
- `pip-audit` against the locked development environment.
- Snyk Open Source scanning when the repository secret `SNYK_TOKEN` is configured. Without the secret, the workflow reports that Snyk is skipped rather than failing unrelated CI.

The application also keeps the existing authentication, same-origin write protection, security headers, runtime-only secrets, upload validation, and subprocess hardening.

## CI/CD

The repository now separates three delivery layers:

1. **Tests:** Python matrix, Windows smoke tests, media integration checks, coverage, packaging checks, and dependency auditing.
2. **E2E:** `.github/workflows/e2e.yml` starts the real Gunicorn dashboard and exercises login, authenticated APIs, settings writes, and cross-origin rejection.
3. **Container delivery:** `.github/workflows/release.yml` builds the production Docker image, publishes it to GHCR, emits SBOM/provenance metadata, and can deploy it over SSH to a configured production host.

Production deployment is gated by the repository variable `AIVF_DEPLOY_ENABLED=true` and uses the `production` GitHub environment. Required deployment secrets are documented below.

### Deployment secrets

Configure these only when using the SSH deployment job:

- `DEPLOY_HOST`
- `DEPLOY_USER`
- `DEPLOY_PATH`
- `DEPLOY_SSH_KEY`
- `DEPLOY_REGISTRY_USER`
- `DEPLOY_REGISTRY_TOKEN`

The remote host must already have Docker, Docker Compose, a writable deployment directory, and a valid `.env` containing the runtime secrets required by the application.

## Coverage policy

The main Python CI workflow uses an 80% minimum coverage gate for its selected high-value v3/runtime modules. Coverage should be increased through targeted regression tests rather than by excluding difficult code paths.

The test suite distinguishes normal unit tests from media integration tests through the `integration` pytest marker.

## Modularization strategy

The repository does not pretend that a microservice split is already complete. The safe modularization completed here is the storage/cache boundary plus the existing spawned-worker boundary.

A real distributed architecture would additionally need durable job ownership, a queue/broker, idempotent job claims, shared storage, worker heartbeats, and deployment coordination. Those are intentionally not simulated with a thin HTTP wrapper because doing so would add failure modes without improving the current single-host workflow.

## Local operation

Basic installation remains:

```bash
python -m venv .venv
python -m pip install -e '.[web,dev]'
```

Optional Redis-backed caching can be enabled by installing the Redis client in the environment and setting `AIVF_REDIS_URL`, for example:

```text
AIVF_REDIS_URL=redis://127.0.0.1:6379/0
```

Without that variable, no Redis server is required.

## Release checklist

Before production deployment, verify:

- CI and E2E workflows are green.
- CodeQL has no new high-severity findings.
- `pip-audit` is clean or known exceptions are documented.
- Snyk is configured when repository policy requires it.
- `FLASK_SECRET_KEY` and `AIVF_DASHBOARD_TOKEN` are set to strong values.
- Docker health checks pass after rollout.
- The remote deployment directory contains the required `.env` and persistent volumes are preserved.
