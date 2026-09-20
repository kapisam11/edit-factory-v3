# Architecture

Edit Factory v3 is a single-host production application with one canonical CLI (`cli.py`, exposed
as `aivf`) and one V3 emotion-first planner/CLI (`v3_cli.py`, exposed as `aivf-v3`) plus one
production dashboard entry point (`wsgi.py`). The implementations for the web entry points now
live in `app/`; the root files are compatibility launchers. Legacy compatibility modules remain
available where existing integrations depend on their import paths. The web dashboard is served
by Flask through one Gunicorn worker.

## Dashboard execution

The Flask application stores durable job state in SQLite and keeps process handles in memory.
Each active job is executed in a dedicated `multiprocessing` process using the `spawn` context.
No process pool is created at module import time.

```text
Browser -> Gunicorn (1 worker) -> Flask
                                  |
                                  +-> SQLite (WAL/busy timeout)
                                  +-> spawned job process -> pipeline -> FFmpeg/FFprobe

V3 planner -> emotion/core -> edit type -> hooks -> clip plan -> music -> retention plan -> independent baseline render -> retention render -> baseline-delta QC -> semantic QC -> artifact readiness -> `v3_renderer_bridge` -> legacy media renderer
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
