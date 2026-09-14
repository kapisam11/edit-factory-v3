# 07 — Troubleshooting

Start here when something does not work.

## `python` cannot find the package

Make sure you are in the repository directory and that the virtual environment is active.

```bash
python -m pip install -e ".[web,dev]"
```

## `ffmpeg` or `ffprobe` not found

Install FFmpeg and make sure both commands work:

```bash
ffmpeg -version
ffprobe -version
```

The application uses FFmpeg for media processing and FFprobe for media inspection/validation.

## The dashboard will not start

Check the logs first. Then confirm the production entry point is `wsgi:app` and that the required Flask/dashboard secrets are present.

For production, use one Gunicorn worker.

## A job is stuck

Check the job state and logs. After a process restart, an active in-memory job may be recorded as `interrupted`. That is intentional: the system does not pretend a worker survived a restart.

## Cancellation does not seem complete

Check whether the worker and FFmpeg processes actually stopped. A successful API response alone is not enough to prove process termination; this is why cancellation should also be tested on the real deployment machine.

## Database errors

SQLite uses WAL mode and a busy timeout for concurrent application/worker access. If you still see database errors, inspect filesystem permissions, disk space, and whether multiple unrelated processes are trying to use the same database.

## A feature is unavailable

Some features are optional. Check `pyproject.toml` for extras such as `beats`, `vision`, `ocr`, `diarization`, `groq`, and `elevenlabs`.

## Need deeper information?

See [02 — How It Works](02-HOW-IT-WORKS.md), [05 — Project Map](05-PROJECT-MAP.md), and [Architecture](ARCHITECTURE.md).
