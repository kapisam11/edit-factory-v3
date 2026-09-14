# Make Your First Video

This is the fastest path from a fresh checkout to a first test run.

## Step 1 — Install

From the repository root:

```bash
python -m venv .venv
```

Activate the environment, then install Edit Factory:

```bash
python -m pip install -e './01-MAIN-CODE[web,dev]'
```

## Step 2 — Check the CLI

```bash
python 01-MAIN-CODE/cli.py --help
aivf --help
```

Both commands should print help without starting a production job.

## Step 3 — Try a small topic

```bash
python 01-MAIN-CODE/cli.py "Minecraft betrayal on SMP"
```

Use `aivf --help` before changing options. The current CLI help is the source of truth for supported flags.

## What should happen?

The pipeline plans the content, creates editing decisions, processes media with FFmpeg, performs quality checks, and writes the configured output package.

Generated media, uploads, state databases, caches, and local tool bundles are runtime data. They are not source code and should not be committed.

## Want the browser dashboard?

Read [04 — Using the Dashboard](04-USING-THE-DASHBOARD.md) after the CLI check passes. The dashboard uses a separate worker process for media jobs and requires the production authentication settings when deployed for real use.
