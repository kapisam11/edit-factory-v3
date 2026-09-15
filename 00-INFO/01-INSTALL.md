# 01 — Install Edit Factory v3

This is the easiest installation path for someone seeing the project for the first time.

## What you need

- Python 3.9 or newer
- FFmpeg and FFprobe installed and available on your PATH
- Git, if you are cloning the project

Optional AI/media features may need extra packages or API keys. Those are explained later.

## Install

From the repository root:

```bash
python -m venv .venv
```

Activate the environment.

**Windows PowerShell**

```powershell
.\.venv\Scripts\Activate.ps1
```

**macOS/Linux**

```bash
source .venv/bin/activate
```

Install the project from the repository root, where `pyproject.toml` lives:

```bash
python -m pip install -e '.[web,dev]'
```

## Check that it installed

Run the main CLI launcher from the repository root:

```bash
python 01-MAIN-CODE/cli.py --help
```

The installed commands are also available as:

```bash
aivf --help
aivf-v3 --help
```

Also check FFmpeg:

```bash
ffmpeg -version
ffprobe -version
```

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

Start with the help commands; they do not run a production workload:

```bash
python 01-MAIN-CODE/cli.py --help
aivf-v3 --help
```

Then use [03 — Using the CLI](03-USING-THE-CLI.md) for an actual job.
