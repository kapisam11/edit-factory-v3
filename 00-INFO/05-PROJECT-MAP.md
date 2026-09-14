# 05 — Project Map

This page answers: **“Which part does what?”**

You normally do not need to edit most files directly.

## Repository map

| Location | Plain-English job |
|---|---|
| `README.md` | The front door: what the project is and where to go next |
| `00-INFO/` | Beginner guides, architecture, operations, deployment, and history |
| `01-MAIN-CODE/` | Core Python application, CLI launchers, package metadata, and dependency lockfile |
| `01-MAIN-CODE/ai_video_factory/` | Core video-production pipeline and supporting Python code |
| `02-WEB-FILES/` | Dashboard runtime, HTML templates, and browser assets |
| `02-WEB-FILES/app/` | Canonical Flask dashboard and WSGI implementation |
| `02-WEB-FILES/templates/` | Flask dashboard HTML/templates |
| `02-WEB-FILES/static/` | Dashboard CSS and browser assets |
| `03-SIDE-CODE/tools/` | Reusable developer/helper tools |
| `03-SIDE-CODE/scripts/` | Checks, verification, and maintenance scripts |
| `04-TESTS/tests/` | Automated tests |
| `05-EXTENSIONS/` | Prompt library, learning data, and hook/scoring resources |
| `06-CONFIG-AND-DEPLOYMENT/` | Docker, Compose, Gunicorn, dependency reference, and configuration |
| `99-ARCHIVE/legacy/` | Retired compatibility/demo material |

## Main runtime entrypoints

The important files are grouped under the numbered folders instead of being scattered across the repository root:

- `01-MAIN-CODE/cli.py` → main Python CLI launcher
- `01-MAIN-CODE/cli_v2.py` → older CLI compatibility path
- `01-MAIN-CODE/dashboard_auth.py` → dashboard authentication support
- `01-MAIN-CODE/dashboard_compat.py` → compatibility dashboard routes
- `01-MAIN-CODE/dashboard_shutdown.py` → active-worker shutdown handling
- `01-MAIN-CODE/dashboard_worker.py` → spawn-safe worker launcher
- `01-MAIN-CODE/web_app_v2.py` → compatibility dashboard import
- `01-MAIN-CODE/wsgi.py` → production WSGI launcher

The canonical web implementation lives under `02-WEB-FILES/app/`. The numbered layout is primarily for human navigation; the package names and runtime paths are preserved so the application and existing integrations keep working.

## Core application

Everything in `01-MAIN-CODE/ai_video_factory/` is core application code. Useful starting modules include:

| File | Responsibility |
|---|---|
| `pipeline.py` | Orchestrates production stages |
| `director.py` | High-level video production workflow |
| `composer.py` | Combines media into a finished composition |
| `model_adapter.py` | AI/model provider adapters |
| `config.py` | Configuration models and safe persistence |
| `asset_manager.py` | Runtime assets and metadata |
| `knowledge.py` / `knowledge_v2.py` | Knowledge and learning data |
| `learning.py` | Learning and feedback logic |
| `learning_recommender.py` | Recommendations from learned results |
| `edit_planner.py` | Editing plans |
| `edit_automation.py` | Automated editing operations |
| `effects_engine.py` | Media effects |
| `music*.py` | Music retrieval, analysis, mixing, and timing |
| `hardware.py` | Capability detection |
| `interactive_review.py` | Human-review helpers |

## Documentation

The human-facing documentation lives under `00-INFO/` and is intentionally numbered in the order a new person is likely to need it:

```text
00 Start Here
01 Install
02 How It Works
03 Using the CLI
04 Using the Dashboard
05 Project Map
06 Production Deployment
07 Troubleshooting
08 Learning System
09 Developer Guide
10 Upgrading
```

Technical reference pages such as architecture, operations, hardening, release checks, structure notes, and history are also kept in `00-INFO/`.

## What not to edit manually

Do not manually edit generated runtime files, databases, uploads, produced media, local model caches, or downloaded tool bundles. Those are runtime data rather than application source code.
