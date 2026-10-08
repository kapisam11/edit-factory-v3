# Architecture

Edit Factory v3 is a single-host production application with one canonical CLI (`cli.py`, exposed
as `aivf`) and one V3 emotion-first planner/CLI (`v3_cli.py`, exposed as `aivf-v3`) plus one
production dashboard entry point (`wsgi.py`). The implementations for the web entry points now
live in `app/`; the root files are compatibility launchers. Legacy compatibility modules remain available where existing integrations depend on their import paths. The web dashboard is served by Flask through one Gunicorn worker. The dashboard route layer delegates job-parameter business validation to app/job_service.py, while SQLite lifecycle and admission rules live in dashboard_store.py.

## Dashboard execution

The Flask application stores durable job state in SQLite and keeps process handles in memory.
Each active job is executed in a dedicated `multiprocessing` process using the `spawn` context.
No process pool is created at module import time.

```text
Browser -> Gunicorn (1 worker) -> Flask
                                  |
                                  +-> SQLite (WAL/busy timeout)
                                  +-> spawned job process -> pipeline -> FFmpeg/FFprobe

V3 planner -> typed/immutable V3Blueprint -> persisted round-trip validation -> typed V3RenderPlan -> v3_renderer_bridge -> legacy media renderer -> independent render QC -> artifact readiness
          -> packaging -> existing production renderer
```

## Lifecycle and recovery

Supported states are `queued`, `running`, `cancelling`, `cancelled`, `done`, `error`, and
`interrupted`. A web-process restart cannot resume an in-memory worker, so outstanding
`queued`, `running`, or `cancelling` jobs are reconciled as `interrupted` rather than being
silently resumed.

## Security boundaries

Production dashboard access is protected by `AIVF_DASHBOARD_TOKEN`. State-changing requests
from an authenticated browser are restricted to the same origin. Session cookies are HttpOnly
and SameSite=Strict, with Secure enabled by `AIVF_COOKIE_SECURE=1` when HTTPS is in use.

Dashboard API credentials are runtime-only. They are not stored in job records, logs, or job
responses. Uploaded files use UUID filenames, an extension allowlist, and FFprobe validation.
Generated package paths are slugged and explicitly contained inside the configured output root.

## Pipelines

`ai_video_factory/pipeline.py` remains the canonical legacy-compatible stage orchestration layer.
The V3 entry point adds `ai_video_factory/v3_pipeline.py`, which creates and persists the
emotion-first V3 blueprint before handing the production brief to the existing renderer. This
keeps one media execution path instead of maintaining two competing FFmpeg/rendering stacks.
Historical engineering notes are retained under `00-INFO/history/`; they are not part of the
supported runtime path.

## Deployment

Use one Gunicorn worker while job execution remains process-local. Docker Compose persists
output, uploads, knowledge data, and SQLite state under `/app/state`. The container requires a
real `FLASK_SECRET_KEY` and dashboard token; it does not ship runtime FFmpeg bundles from the
repository.

## Enforced boundaries

The V3 planner owns creative decisions: emotion, hooks, edit type, pacing, clip purposes, overlays, music intent, retention intent, and packaging metadata. It does not invoke FFmpeg.

The V3 blueprint is immutable after construction. Rendering receives a derived V3RenderPlan; the legacy renderer is reached only through v3_renderer_bridge.py. The persisted blueprint is read back and validated before rendering, so JSON serialization is part of the contract boundary.

## Job lifecycle

Durable status transitions are defined in ai_video_factory/job_state.py, checked by DashboardStore, and guarded by a SQLite trigger in the dashboard database. A queued job is atomically claimed before a worker process is spawned. A worker may only reach done after its final artifact passes media validation.

```text
queued -> running -> done
              \-> error
              \-> interrupted
queued/running -> cancelling -> cancelled
error/interrupted -> queued (bounded retry)
```

Retry attempts are bounded and deterministic failures are not retried by the dashboard retry policy.

## Configuration and capabilities

High-value execution settings such as media/provider timeouts are represented by the typed RuntimeConfig boundary. Optional runtime capabilities are reported by ai_video_factory.runtime_capabilities; feature code can fail with an actionable installation requirement instead of an opaque import error.

## Media safety

Production FFmpeg/FFprobe work is routed through validated argv-based helpers with bounded timeouts. Temporary artifacts use atomic/finalization patterns where practical, and failure paths remove known partial outputs before reporting failure.

### Final hardening boundary (2026-10-08)

Production schema mutation is deployment-owned: aivf-db-migrate creates and verifies lifecycle tables, indexes, and status triggers before the application starts. Request and worker paths fail closed when the schema is incomplete.

Each worker execution receives an attempt ID and lease token and writes only inside an attempt workspace. Publication performs a filesystem move followed by a second transactional SQLite fence check. Cancellation clears the lease before terminating the worker, so a stale worker cannot promote an attempt to DONE.

Resource admission accounts for input/output budgets, CPU/memory classes, disk reservations, physical free space, and the total storage quota. The janitor owns retention and SQLite maintenance independently of request traffic.
