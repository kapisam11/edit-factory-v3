# Changelog

## 3.0.0 — 2026-09-14

### V3 creative system
- Added the emotion-first V3 blueprint engine and `aivf-v3` CLI.
- Added exactly 40 tracked V3 creative, retention, quality, platform, and packaging capabilities. The registry now labels each capability as deterministic, hybrid, or heuristic instead of implying that heuristic checks are equivalent to human editorial review.
- Added core emotional idea analysis, single edit-type locking, ranked hooks, adaptive clip planning, overlay rules, music/beat/drop planning, retention events, human-editor QC, platform variants, and upload metadata generation.
- Added V3 production integration that persists `v3_blueprint.json` before handing the brief to the existing media renderer.
- Added deterministic, offline-safe V3 planning so creative planning does not require an external AI provider.

### Verification
- Added V3 engine and CLI regression tests while preserving the existing production test suite.
- CI verification covers the supported Python matrix, Windows smoke coverage, Ruff, compile checks, CLI smoke tests, wheel/install checks, full pytest/coverage, dependency auditing, and Docker.

### Branding and documentation
- Updated the primary README, installation guide, start-here guide, architecture, project map, upgrade guide, license notes, security policy, contributing guide, and web/main-code documentation to identify the supported product as Edit Factory v3.
- Compatibility modules with historical versioned filenames remain available only where their import paths are part of the compatibility surface; they do not define the current product version.

## Unreleased — final audit cleanup

### Reliability and security
- Removed the obsolete duplicate dashboard-operations module after consolidating cleanup and lifecycle handling in the supported dashboard path.
- Kept dashboard job execution behind the spawn-safe `dashboard_worker.py` process boundary.
- Retained real OS-level worker cancellation and terminal-state protection.
- Retained SQLite WAL, busy timeouts, append-only job logs, startup reconciliation, upload validation, path containment, and runtime-only API credentials.
- Kept authenticated dashboard access, same-origin protection for mutating browser requests, and security headers.
- Hardened the FFmpeg/FFprobe boundary with explicit executable validation, argv-only execution, bounded timeouts, media existence checks, and strict post-render probing.
- Fixed the render-engine regression tests to exercise real media-path validation and executable rejection rather than relying on permissive legacy behavior.
- Aligned the supported Python floor across package metadata, CI, and operational documentation to Python 3.10–3.12.

### Tooling and deployment
- Docker contains exactly the modules required by the production WSGI entrypoint.
- CI validates Python 3.10–3.12, Windows-sensitive modules, dependency security, CLI entry points, and the production Docker image.
- Local/generated state, media, models, and tool bundles remain excluded from source control.
- `aivf` points to the canonical `cli.py` entry point; legacy compatibility launchers remain available where required.

### Documentation
- Production architecture documentation now names the V3 planner and one supported dashboard runtime path.
- Historical documents and compatibility code remain explicitly separated from the supported runtime; they are not the product version.
- The remaining release gate is target-host deployment acceptance after a fresh green CI run.

## 2.3.0 — 2026-09-03

### Reliability and security
- Moved dashboard job execution into the spawn-safe `dashboard_worker.py` process boundary.
- Added real OS-level worker cancellation instead of relying on `Future.cancel()`.
- Reconciled jobs that were left `running` after a dashboard restart as `interrupted`.
- Enabled SQLite WAL mode and busy timeouts for concurrent dashboard/worker access.
- Added timing-safe dashboard token comparison with `hmac.compare_digest`.
- Added video signature validation and optional `ffprobe` validation for uploads.
- Centralized workflow and target-duration validation for CLI and dashboard.
- Added graceful worker shutdown handling and single-web-worker deployment guidance.
- Added automatic output retention cleanup.
- Added security headers and production health reporting.

### Tooling and deployment
- CI and packaging support Python 3.9 through 3.12.
- Added Dockerfile, Docker Compose, Docker context exclusions, and Gunicorn configuration.
- Removed runtime FFmpeg/model bundles and generated output from the current repository tree.
- Tightened `.gitignore` for runtime state, generated media, local tools, and models.
- Added structured CLI logging, tracebacks, progress/ETA, and batch CSV/JSON support.

### Documentation
- Consolidated operational documentation under `00-INFO/`.
- Historical Git objects containing old binaries are not part of the supported source tree; target-host acceptance remains a separate release gate.
