# 04 — Using the Dashboard

The dashboard is the browser **control panel** for Edit Factory. It is the main place to start jobs, watch the queue, cancel work, inspect generated packages, and change supported runtime defaults.

The **V3 Production** workflow is also controlled from this panel. Select V3 to expose platform, audience, BPM, edit type, creative context, and optional OCR/object-detection/diarization controls. The dashboard disables optional controls that are not installed in the production runtime. V3 jobs require an uploaded source video and are dispatched to `ai_video_factory.v3_pipeline.run_v3_pipeline()`; completed packages expose the V3 readiness report and canonical final-video preview.

## Reliability and production overview

The dashboard header includes a compact, expandable **Latest reliability & production updates** overview linking to the underlying project guides. The Quick Actions panel also links to the Autonomous Controls page; the sign-in and autonomous pages share the Edit Factory V3 branding. The overview summarizes these areas:

- validated render output and protection of the last known-good final artifact
- persistent job attempts, restart reconciliation, and stale-worker fencing
- the six evidence-driven readiness states: `PASS`, `IMPLEMENTED`, `CI`, `ENVIRONMENT`, `MANUAL`, and `BLOCKED`
- the distinction between the development launcher (`START-ALL.bat`) and the pinned-image production launcher (`START-PRODUCTION.bat`), including explicit database migration
- localhost-only exposure defaults and conservative CPU, memory, concurrency, upload, and disk safeguards
- per-platform publishing status/reconciliation and the need to verify real platform accounts separately
- rights evidence, factuality checks, and human approval before publishing
- links to the Windows, Docker, security, and end-to-end CI verification

This is a **static feature overview**, not a live release certificate. The cards do not query the user's host, re-run readiness checks, grant publishing permissions, or confirm a remote post. Use the live health/publishing panels and CI links for current status, then complete host and account acceptance before enabling unattended public publishing.

## Components

```text
Browser control panel
  ↓
02-WEB-FILES/templates/index.html
  ↓
02-WEB-FILES/app/web_app_v3.py
  ↓
01-MAIN-CODE/dashboard_worker.py
  ↓
ai_video_factory.v3_pipeline.run_v3_pipeline() for V3 jobs
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

The control panel supports raw-video uploads, the canonical `default`, `fast`, `package_only`, and `v3` workflows, platform-aware V3 durations from 8–180 seconds, V3 audience/BPM/edit-type/context controls, runtime capability gating, Groq and QC controls, queue monitoring, cancellation, retry, live preview, presets, persistent defaults, and generated-package inspection.

Older workflow aliases such as `director` and `legacy` remain accepted for compatibility but are normalized to the current canonical workflow names.

## Settings

The Settings panel can persist:

- default target duration
- default workflow
- default V3 platform
- default V3 audience
- default V3 BPM
- default Skip-QC behavior
- default Groq usage
- maximum concurrent jobs

Production deployments keep semantic V3 QC enabled. Disabling semantic QC or Skip-QC is development/test-only and requires the explicit QC override.

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
2. Confirm `default`, `fast`, `package_only`, and `v3` workflow choices appear.
3. Select V3 and confirm unavailable optional intelligence controls are disabled.
4. Create a V3 job with a supported raw video.
5. Watch queue state and live logs.
6. Cancel a queued/running job and verify `cancelled`.
7. Open a completed package and test video, script, thumbnails, and files.
8. Restart the server and verify active jobs become `interrupted`.

A successful Python import, Docker build, or unit test run alone is not proof that the control panel works correctly on the target deployment.


## Advanced operations console

The owner/admin-only advanced console is an operational troubleshooting view, not a publishing or deletion backdoor. From any authenticated dashboard page, an admin can open the unlock dialog by clicking the small **V3** brand badge five times quickly, then enter the configured extra unlock code. Access expires after 15 minutes; normal dashboard authentication and the admin-role check still apply.

The console includes:
- Runtime health checks for SQLite integrity, FFmpeg/FFprobe, process uptime and memory.
- Storage and workload summaries, HTTP request totals, 5xx responses and recent p95 request latency when metrics are available.
- An operations feed combining persisted job output, job lifecycle events and administrative audit records. The feed refreshes every five seconds while live mode is enabled.
- Search, severity and source filters, pause/resume, CSV export and copy-visible actions. These only change the view or exported copy; they never delete or modify stored records.
- A recent failed-job table, administrative audit trail and a downloadable redacted diagnostics snapshot.

The log endpoint returns a bounded set of existing database records. It does not create tables on a request, and its response omits audit metadata, raw request headers and credential fields. Error text is redacted before being displayed. The feed is not a replacement for operating-system/container logs or a durable centralized logging service.

