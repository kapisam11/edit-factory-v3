# 09 — Developer Guide

This is for someone who wants to change the application.

## Before changing code

Read these first:

1. [02 — How It Works](02-HOW-IT-WORKS.md)
2. [05 — Project Map](05-PROJECT-MAP.md)
3. [Architecture](ARCHITECTURE.md)
4. [Operations](OPERATIONS.md)

## Where to make changes

### New pipeline behavior

Start with `ai_video_factory/pipeline.py` and the related stage/module instead of adding more logic to the web application.

### Dashboard behavior

Start with `web_app_v2.py` and the dashboard support modules. Heavy work should continue to run through the dedicated worker boundary.

### AI provider behavior

Use `ai_video_factory/model_adapter.py`. Keep provider-specific authentication/routing isolated rather than spreading API calls throughout the application.

### Media rendering

Use the rendering/media modules and keep FFmpeg invocation behind the existing validation, timeout, and output-checking patterns.

## Tests

Run the automated suite:

```bash
pytest
```

Run lint:

```bash
ruff check .
```

For changes that affect packaging or deployment, also test the Docker build and the production import path.

## Important rule

Do not copy implementation instructions from old historical notes into current code without checking the current source. Historical documents are background material, not the current source of truth.

## Pull requests

Keep changes focused and explain:

- what changed
- why it changed
- how it was tested
- whether production-host testing is still required
