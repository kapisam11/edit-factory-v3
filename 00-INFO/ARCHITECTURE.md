# Architecture

Edit Factory v2 is a single-host production application with one canonical CLI (`cli.py`, exposed
as `aivf`) and one production dashboard entry point (`wsgi.py`). The implementations for these
entry points now live in `app/`; the root files are compatibility launchers. `cli_v2.py` is retained
only for legacy scripts that still invoke the older command surface. The web dashboard is served
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

`ai_video_factory/pipeline.py` is the canonical stage orchestration layer. Historical engineering
notes are retained under `00-INFO/history/`; they are not part of the supported runtime path. The
canonical installed command is `aivf` -> `cli.py`; `cli_v2.py` is deprecated compatibility code
and should not receive new features.

## Deployment

Use one Gunicorn worker while job execution remains process-local. Docker Compose persists
output, uploads, knowledge data, and SQLite state under `/app/state`. The container requires a
real `FLASK_SECRET_KEY` and dashboard token; it does not ship runtime FFmpeg bundles from the
repository.
