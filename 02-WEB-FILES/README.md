# Web files

This folder contains everything needed for the browser control panel.

- `app/` — Flask dashboard/API, WSGI entry point, CLI compatibility layer, and dashboard worker
- `templates/` — browser control-panel HTML
- `static/` — dashboard CSS and static assets

## Control panel architecture

```text
Browser
  ↓
02-WEB-FILES/templates/index.html
  ↓
02-WEB-FILES/app/web_app_v2.py
  ↓
01-MAIN-CODE/dashboard_compat.py
  ↓
SQLite job state + spawned dashboard worker
  ↓
01-MAIN-CODE/ai_video_factory pipeline
```

The dashboard is the browser-facing control plane for the application. It creates jobs, uploads optional raw footage, selects the workflow, monitors the queue and live logs, cancels jobs, changes supported defaults, and inspects generated packages.

The current control-panel API includes job creation/status/logs, queue status, settings, presets, package browsing, script editing, package file serving, cancellation, and health reporting.

The launcher compatibility files that preserve the project's command/import interfaces live in `01-MAIN-CODE/`.
