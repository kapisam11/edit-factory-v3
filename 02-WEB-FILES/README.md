# Web files

This folder contains everything needed for the Edit Factory v3 browser control panel.

- `app/` — Flask dashboard/API, WSGI entry point, CLI compatibility layer, and dashboard worker
- `templates/` — browser control-panel HTML
- `static/` — dashboard CSS and static assets

## Control panel architecture

```text
Browser
  ↓
02-WEB-FILES/templates/index.html
  ↓
02-WEB-FILES/app/web_app.py
  ↓
01-MAIN-CODE/dashboard_compat.py
  ↓
SQLite job state + spawned dashboard worker
  ↓
01-MAIN-CODE/ai_video_factory V3 pipeline
  ↓
existing production renderer
```

The dashboard is the browser-facing control plane for the application. It creates jobs, uploads optional raw footage, selects the workflow, monitors the queue and live logs, cancels jobs, changes supported defaults, and inspects generated packages.

The current control-panel API includes job creation/status/logs, queue status, settings, presets, package browsing, script editing, package file serving, cancellation, and health reporting.

The V3 emotion-first planner is available through the `aivf-v3` command and the `v3_pipeline` integration without requiring a second media-rendering stack.

The launcher compatibility files that preserve the project's command/import interfaces live in `01-MAIN-CODE/`.
