# Edit Factory v2 — Start Here

Welcome. This project turns a topic and optional raw video into an edited short-video package.

You do **not** need to understand every Python file before using it. Follow the three-step path below, then use the project map when you need more detail.

## What this project does

```text
Topic + optional video
        ↓
 AI planning/research
        ↓
 script + hooks
        ↓
 editing decisions
        ↓
 FFmpeg processing
        ↓
 final video package
```

The web dashboard adds a browser interface for creating jobs and watching progress.

## Your first 5 minutes

### 1. Install

Follow [01 — Install](01-INSTALL.md).

### 2. Confirm the CLI works

From the repository root:

```bash
python 01-MAIN-CODE/cli.py --help
aivf --help
```

Both commands should print help without starting a production job.

### 3. Make your first small job

Follow [Make Your First Video](FIRST-VIDEO.md) for the complete first-run path.

A simple test topic is:

```bash
python 01-MAIN-CODE/cli.py "Minecraft betrayal on SMP"
```

For the exact options available in your installed version, run `aivf --help` first.

## Important: what is safe to change?

For a first-time user, the safest approach is:

- Read and follow `00-INFO/` before changing runtime code.
- Treat `01-MAIN-CODE/ai_video_factory/` as the core application.
- Treat `02-WEB-FILES/` as the browser/dashboard side.
- Do not commit generated videos, uploads, databases, caches, downloaded models, or local tool bundles.
- Never put real API keys or production secrets into tracked files.

## The important entry points

### `aivf`
The normal installed command-line interface.

### `01-MAIN-CODE/cli.py`
The main direct Python CLI launcher. Run it from the repository root with `python 01-MAIN-CODE/cli.py ...`.

### `02-WEB-FILES/app/wsgi.py`
The production WSGI implementation used by Gunicorn.

### `02-WEB-FILES/app/web_app_v2.py`
The main Flask dashboard implementation.

### `01-MAIN-CODE/dashboard_worker.py`
The spawn-safe worker launcher used by the dashboard runtime.

## The project folders

```text
00-INFO/                    ← documentation, guides, architecture, operations
01-MAIN-CODE/               ← core Python application and command-line runtime
02-WEB-FILES/               ← dashboard application, HTML templates, CSS/assets
03-SIDE-CODE/               ← reusable tools and maintenance scripts
04-TESTS/                   ← automated tests
05-EXTENSIONS/              ← prompts, learning data, scoring resources
06-CONFIG-AND-DEPLOYMENT/  ← Docker, Gunicorn, Compose, and configuration
99-ARCHIVE/                 ← retired material
```

The repository root intentionally contains only `README.md` plus hidden Git/editor configuration files. Runtime source code and resources are inside the numbered folders so the GitHub front page stays easy to understand.

## What is inside the main folders?

`01-MAIN-CODE/` contains the core `ai_video_factory/` package plus the Python launchers and package metadata needed to install and run the project.

`02-WEB-FILES/` contains the Flask dashboard implementation in `app/`, plus `templates/` and `static/` for the browser UI.

`03-SIDE-CODE/` contains helper tooling such as reusable developer utilities and maintenance scripts. These are not the main production pipeline.

`04-TESTS/` contains the test suite used by CI.

`05-EXTENSIONS/` contains optional resources such as prompts, learning data, and related scoring/extension material.

`06-CONFIG-AND-DEPLOYMENT/` contains deployment and environment-facing configuration such as Docker, Compose, Gunicorn, dependency reference files, and the example configuration.

`99-ARCHIVE/` contains retired compatibility or historical material that is not part of the normal workflow.

## Where generated data goes

Generated videos and job state live in runtime directories such as `output/`, `uploads/`, and `state/`. Those are not source code and should not be committed to Git.

## Need a specific answer?

| I want to... | Read this |
|---|---|
| Install and run it | `01-INSTALL.md` |
| Make my first video | `FIRST-VIDEO.md` |
| Understand the pipeline | `02-HOW-IT-WORKS.md` |
| Use the CLI | `03-USING-THE-CLI.md` |
| Run the dashboard | `04-USING-THE-DASHBOARD.md` |
| Find a file | `05-PROJECT-MAP.md` |
| Deploy it | `06-PRODUCTION-DEPLOYMENT.md` |
| Fix a problem | `07-TROUBLESHOOTING.md` |
| Understand learning | `08-LEARNING-SYSTEM.md` |
| Change code | `09-DEVELOPER-GUIDE.md` |
| Upgrade | `10-UPGRADING.md` |
