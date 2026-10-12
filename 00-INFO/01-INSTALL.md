# 01 — Install Edit Factory v3

This is the recommended installation path for a new checkout.

## What you need

- **Python 3.12** for the production Windows workflow. The source supports Python 3.10+ and CI checks Python 3.10–3.14, but the production Docker image and Windows acceptance target are based on Python 3.12.
- FFmpeg and FFprobe available on your PATH for local CLI renders.
- Git, if you are cloning the project.
- `uv`, so the committed `uv.lock` is used for a reproducible install.

Optional AI/media features may need extra packages or API keys.

## Install from the repository root

Run the same locked dependency setup used by CI:

```powershell
uv sync --frozen --python 3.12 --extra web --extra dev
```

This creates/reuses the project virtual environment, installs the configured extras, and refuses to silently resolve a different dependency graph when the lockfile is stale. Run the command after pulling a new version or changing dependencies.

## Check that it installed

```powershell
uv run python 01-MAIN-CODE/cli.py --help
uv run aivf --help
uv run aivf-v3 --help
uv run aivf-folder-publisher --help
uv run aivf-publish --help
```

Also check FFmpeg:

```powershell
ffmpeg -version
ffprobe -version
```

The editable pip install path (`python -m pip install -e '.[web,dev]'`) remains available for development, but it does not make pip consume `uv.lock`; prefer the frozen `uv sync` workflow for repeatable production setup.

## API keys

Do not put real API keys into Git or commit them into configuration files.

Typical optional environment variables include:

```text
GROQ_API_KEY
ELEVENLABS_API_KEY
OPENAI_API_KEY
```

The production dashboard also expects a dashboard token and a Flask secret. See [06 — Production Deployment](06-PRODUCTION-DEPLOYMENT.md).

## First test

Start with the help commands; they do not run a production workload. Then proceed to the Windows acceptance checklist only after the initial Python checks have completed.
