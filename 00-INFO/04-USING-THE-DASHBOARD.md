# 04 — Using the Dashboard

The dashboard is the browser **control panel** for Edit Factory. It is the main place to start jobs, watch the queue, cancel work, inspect generated packages, and change supported runtime defaults.

## Components

```text
Browser control panel
  ↓
02-WEB-FILES/templates/index.html
  ↓
02-WEB-FILES/app/web_app_v2.py
  ↓
01-MAIN-CODE/dashboard_compat.py
  ↓
SQLite + spawned worker
  ↓
01-MAIN-CODE/ai_video_factory pipeline
```

The production WSGI implementation lives at `02-WEB-FILES/app/wsgi.py` and the Gunicorn configuration lives at `06-CONFIG-AND-DEPLOYMENT/gunicorn.conf.py`.

Start the production dashboard from the repository root with:

```bash
gunicorn -c 06-CONFIG-AND-DEPLOYMENT/gunicorn.conf.py wsgi:app
```

## Normal flow

```text
Open dashboard → Authenticate → Configure job → Start
      ↓
Queue + live logs → Complete / Cancel / Error
      ↓
Open package → Preview video / edit script / inspect thumbnails / files
```

## Current controls

The control panel supports an optional raw-video upload, a 15–120 second target duration, the canonical `default`, `fast`, and `package_only` workflows, Groq and QC toggles, queue monitoring, cancellation, presets, persistent defaults, and generated-package inspection.

Older workflow aliases such as `director` and `legacy` remain accepted for compatibility but are normalized to the current canonical workflow names.

## Settings

The Settings panel can persist:

- default target duration
- default workflow
- default Skip-QC behavior
- default Groq usage
- maximum concurrent jobs

API keys can be entered from the control panel. Key values are held in process memory and are not returned by the settings endpoint. The panel only reports whether a key is configured.

The upload request limit is controlled by `AIVF_MAX_UPLOAD_MB` when the Flask application starts. The dashboard displays that value but does not pretend it is dynamically editable.

## Queue and cancellation

Heavy media work runs in a spawned worker process. The control panel persists job state in SQLite and dispatches queued work according to the configured concurrency limit.

The **Cancel Job** action terminates the active worker process and records `cancelled`. Terminal job states are protected against late worker updates.

## Upload validation

Raw video uploads are checked for supported extension, request size, available disk space, and an actual video stream. The current media checks reject invalid streams, dimensions above 7680×7680, and durations above one hour.

## Generated packages

The package browser can preview the rendered video, load and save `script.txt`, display thumbnail variants discovered in the package, and list the package's files. Package paths are resolved under the configured output root before files are served.

## Restart behavior

Workers are intentionally process-local. A server restart cannot resume them. Outstanding `queued`, `running`, and `cancelling` jobs are reconciled as `interrupted` so the control panel reflects the actual state.

## Security

Production deployments require dashboard authentication and a real Flask secret key. State-changing browser requests are checked for same-origin protection, security headers are emitted, and job creation is rate-limited.

For production, keep the documented single-host Gunicorn worker-process configuration. Increasing the number of Gunicorn processes requires a separate redesign of process ownership and queue coordination.

## Upgrade verification

After upgrading, verify the browser control panel itself:

1. Open the dashboard and confirm settings load.
2. Confirm the three current workflow choices appear.
3. Create a job without raw video.
4. Create a job with a supported raw video.
5. Watch queue state and live logs.
6. Cancel a queued/running job and verify `cancelled`.
7. Open a completed package and test video, script, thumbnails, and files.
8. Restart the server and verify active jobs become `interrupted`.

A successful Python import, Docker build, or unit test run alone is not proof that the control panel works correctly on the target deployment.
