# 02 — How It Works

You can think of Edit Factory as a small production line.

```text
INPUT
  ↓
Topic / raw video
  ↓
PLANNING
  ↓
Research + script + editing plan
  ↓
ANALYSIS
  ↓
Scene / audio / visual signals when enabled
  ↓
RENDER
  ↓
FFmpeg / FFprobe
  ↓
OUTPUT
  ↓
Video package + logs + metadata
```

## What happens in the dashboard

The browser talks to the Flask application through `wsgi.py`.

The web process stores durable job information in SQLite. A job is then handed to a separate spawned worker process so heavy media work does not run inside the web request itself.

```text
Browser
  ↓
Gunicorn
  ↓
Flask dashboard/API
  ├── SQLite job state
  └── spawned worker process
          ↓
       pipeline
          ↓
    FFmpeg / FFprobe
```

The web application keeps process handles in memory, while the durable job record remains in SQLite. One Gunicorn worker is the supported deployment model because the worker bookkeeping is intentionally process-local.

## Job states

A job can move through states such as:

```text
queued → running → done
                 ↘ error
                 ↘ cancelling → cancelled

restart during active work → interrupted
```

The application records logs in a dedicated `job_logs` table rather than repeatedly rewriting one large JSON log field.

## Where the safety checks happen

Uploads are given UUID-backed names, checked against an allowed extension set, and validated with FFprobe when available.

Output package paths are slugged and contained under the configured output directory.

Dashboard credentials are runtime-only. They are not supposed to be stored in job records, logs, or ordinary job API responses.

## Where the AI fits

The AI/model layer is not the video renderer itself. It helps create or improve plans, scripts, research, and other decisions. The media layer still performs the actual file processing with FFmpeg/FFprobe.

That separation makes it possible to change AI providers without redesigning the entire renderer.

For the deeper technical explanation, see [Architecture](ARCHITECTURE.md).
