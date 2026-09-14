# 10 — Upgrading

Use this guide when you already have an older Edit Factory checkout.

## 1. Back up runtime data

Back up generated outputs and persistent state before replacing the application. At minimum, preserve your configured `AIVF_STATE_DIR`, `AIVF_UPLOAD_DIR`, and `AIVF_OUTPUT_DIR` data when those locations are outside the checkout.

## 2. Update the source

Fetch the new repository version and use the current `README.md` and numbered documentation as the source of truth.

Do not blindly copy old commands or old dashboard files from earlier versions. The project has moved through compatibility launchers and the browser dashboard has also gained queue management, package browsing, cancellation, validated settings, upload validation, and production hardening.

The browser dashboard is the application's **control panel**. The current UI lives in:

```text
02-WEB-FILES/templates/index.html
```

The current Flask dashboard/API implementation lives in:

```text
02-WEB-FILES/app/web_app_v2.py
```

Compatibility routes and lifecycle management live in:

```text
01-MAIN-CODE/dashboard_compat.py
```

## 3. Reinstall dependencies

From the updated checkout:

```bash
python -m pip install -e ".[web,dev]"
```

Optional development tooling should be provisioned before runtime. Application code must not rely on installing Python packages or downloading NLP models while a job is executing.

## 4. Check the CLI

The canonical CLI remains:

```bash
python 01-MAIN-CODE/cli.py --help
```

The installed entry point can also be checked with:

```bash
aivf --help
```

## 5. Check the dashboard control panel

The production WSGI implementation is:

```text
02-WEB-FILES/app/wsgi.py
```

Use the supported single-host Gunicorn configuration:

```bash
gunicorn -c 06-CONFIG-AND-DEPLOYMENT/gunicorn.conf.py wsgi:app
```

The current browser control panel supports:

- creating production jobs with an optional raw video upload
- target durations from 15 to 120 seconds
- the canonical `default`, `fast`, and `package_only` workflows
- Groq and QC toggles
- persistent defaults for duration, workflow, Groq, QC, and maximum concurrent jobs
- in-memory API-key configuration without returning key values from the settings endpoint
- live job logs and queue/status monitoring
- real job cancellation
- generated-package browsing, file access, script editing, thumbnail viewing, and video preview

The upload request limit is controlled by the server environment variable `AIVF_MAX_UPLOAD_MB`. It is displayed by the control panel but is not a runtime-editable dashboard setting.

## 6. Runtime behavior to expect after an upgrade

Jobs are persisted in SQLite. Heavy media work runs in a spawned worker process, while the browser control panel controls the queue and job lifecycle.

A server restart cannot resume an in-memory worker. Outstanding `queued`, `running`, and `cancelling` jobs are reconciled as `interrupted` so the control panel does not falsely report them as active.

The production web deployment is intentionally single-host/single-worker at the process level. Do not increase the Gunicorn process count unless job ownership and lifecycle coordination are redesigned for multiple web processes.

## 7. Run the dashboard smoke test

Before trusting the upgrade, verify the **control panel itself**, not just the Python import:

1. Open the dashboard and confirm settings load.
2. Confirm the production form uses the current workflow names.
3. Create a job without a raw video.
4. Create a job with a supported raw video file.
5. Confirm queue/status updates and live logs.
6. Cancel a running or queued job and confirm it becomes `cancelled`.
7. Open a completed package and verify the video, files, script, and thumbnails.
8. Restart the server and confirm previously active jobs are shown as `interrupted`.

For production hosts, also perform the cancellation, restart, persistence, and shutdown checks described in [06 — Production Deployment](06-PRODUCTION-DEPLOYMENT.md).

## If something changed unexpectedly

Read [04 — Using the Dashboard](04-USING-THE-DASHBOARD.md), [07 — Troubleshooting](07-TROUBLESHOOTING.md), and [05 — Project Map](05-PROJECT-MAP.md) before changing code.

## Git history cleanup is separate

Normal upgrades do not rewrite Git history. Historical secrets or unwanted objects require a separate repository-history purge procedure with explicit approval because it changes commit IDs and may require coordinated force-pushes.
