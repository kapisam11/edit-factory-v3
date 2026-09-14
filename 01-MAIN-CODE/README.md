# Main code

This folder contains the real Python application and command-line runtime.

- `cli.py` — normal CLI launcher
- `cli_v2.py` — older CLI compatibility path
- `wsgi.py` — production web launcher
- `web_app_v2.py` — compatibility dashboard import
- `dashboard_auth.py` — dashboard authentication
- `dashboard_compat.py` — dashboard compatibility routes
- `dashboard_shutdown.py` — active-worker shutdown handling
- `dashboard_worker.py` — spawn-safe worker launcher
- `ai_video_factory/` — core video-production package
- `knowledge_base_v2/` — runtime learning data seed
- `pyproject.toml` — package metadata and dependencies
- `uv.lock` — locked dependency versions

The web implementation itself lives in `02-WEB-FILES/app/`. The small launchers here keep the Python entry points importable while the repository stays organized for humans.
