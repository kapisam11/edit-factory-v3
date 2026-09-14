# Main code

This folder contains the real Edit Factory v3 Python application and command-line runtime.

- `cli.py` — normal production CLI launcher
- `cli_v3.py` — V3 production compatibility CLI
- `v3_cli.py` — V3 emotion-first planning CLI
- `wsgi.py` — production web launcher
- `dashboard_auth.py` — dashboard authentication
- `dashboard_compat.py` — dashboard compatibility routes
- `dashboard_shutdown.py` — active-worker shutdown handling
- `dashboard_worker.py` — spawn-safe worker launcher
- `ai_video_factory/` — core video-production package
- `ai_video_factory/v3_engine.py` — V3 creative/retention/QC blueprint engine
- `ai_video_factory/v3_pipeline.py` — V3 planning-to-renderer integration
- `knowledge_base_v3/` — V3 runtime learning data
- `pyproject.toml` — V3 package metadata and dependencies
- `uv.lock` — locked dependency versions

All supported runtime modules and user-facing entry points use V3 naming. Historical version identifiers are not part of the current runtime contract.

The web implementation itself lives in `02-WEB-FILES/app/`. The launchers here keep the Python entry points importable while the repository stays organized for humans.
