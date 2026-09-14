# Main code

This folder contains the real Edit Factory v3 Python application and command-line runtime.

- `cli.py` — normal production CLI launcher
- `v3_cli.py` — V3 emotion-first planning CLI
- `wsgi.py` — production web launcher
- `dashboard_auth.py` — dashboard authentication
- `dashboard_compat.py` — dashboard compatibility routes
- `dashboard_shutdown.py` — active-worker shutdown handling
- `dashboard_worker.py` — spawn-safe worker launcher
- `ai_video_factory/` — core video-production package
- `ai_video_factory/v3_engine.py` — V3 creative/retention/QC blueprint engine
- `ai_video_factory/v3_pipeline.py` — V3 planning-to-renderer integration
- `knowledge_base_v2/` — legacy runtime learning data retained for compatibility
- `pyproject.toml` — V3 package metadata and dependencies
- `uv.lock` — locked dependency versions

Compatibility modules with historical versioned filenames remain in the tree where changing their import paths would break existing integrations. They are implementation compatibility details, not the product version. The supported product and package version is Edit Factory v3.

The web implementation itself lives in `02-WEB-FILES/app/`. The launchers here keep the Python entry points importable while the repository stays organized for humans.
