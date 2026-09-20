"""AI Video Factory production dashboard.

Single-host architecture: Flask + SQLite + one spawned process per active job.
Secrets stay in process memory and are never persisted in job records.
"""
import json
import logging
import multiprocessing
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from ai_video_factory.validation import normalize_workflow, validate_target_seconds, validate_v3_target_seconds

APP_DIR = Path(__file__).resolve().parent
SOURCE_WEB_DIR = APP_DIR.parent if (APP_DIR.parent / "templates").is_dir() else None
INSTALLED_WEB_DIR = Path(sys.prefix) / "share" / "ai-video-factory"
BASE_DIR = SOURCE_WEB_DIR or INSTALLED_WEB_DIR
if not (BASE_DIR / "templates").is_dir() or not (BASE_DIR / "static").is_dir():
    BASE_DIR = APP_DIR

STATE_DIR = Path(os.environ.get("AIVF_STATE_DIR", BASE_DIR / "state")).resolve()
UPLOAD_FOLDER = Path(os.environ.get("AIVF_UPLOAD_DIR", BASE_DIR / "uploads")).resolve()
OUTPUT_FOLDER = Path(os.environ.get("AIVF_OUTPUT_DIR", BASE_DIR / "output")).resolve()
DB_PATH = STATE_DIR / "jobs.db"
for directory in (STATE_DIR, UPLOAD_FOLDER, OUTPUT_FOLDER):
    directory.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
configured_secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
app.config.update(
    MAX_CONTENT_LENGTH=int(os.environ.get("AIVF_MAX_UPLOAD_MB", "500")) * 1024 * 1024,
    SECRET_KEY=configured_secret or None,
)

logger = logging.getLogger("web_app_v3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
SECRET_PARAM_KEYS = {"groq_key", "model_key", "elevenlabs_key", "diarization_token"}
TERMINAL_STATUSES = {"done", "error", "cancelled", "interrupted"}
SETTINGS_SCHEMA = {
    "default_target_seconds": ("float", 15.0, 120.0),
    "default_workflow": ("workflow", None, None),
    "default_skip_qc": ("bool", None, None),
    "default_use_groq": ("bool", None, None),
    "default_v3_platform": ("text", 1, 64),
    "default_v3_audience": ("text", 1, 500),
    "default_v3_bpm": ("int", 40, 240),
    "max_upload_mb": ("int", 1, 5000),
    "max_concurrent_jobs": ("int", 1, 8),
}
_runtime_secrets: Dict[str, Dict[str, str]] = {}
_runtime_default_secrets: Dict[str, str] = {key: "" for key in SECRET_PARAM_KEYS}
_active_processes: Dict[str, multiprocessing.Process] = {}
_active_processes_lock = threading.RLock()
_package_cache_lock = threading.RLock()
_package_cache: Optional[tuple[float, list]] = None
_PACKAGE_CACHE_TTL = 2.0
_SQLITE_WRITE_RETRIES = 3
_SQLITE_RETRY_DELAY_SECONDS = 0.05
_MAX_QUEUED_JOBS = max(1, int(os.environ.get("AIVF_MAX_QUEUED_JOBS", "20")))
_MIN_FREE_DISK_BYTES = max(
    256 * 1024 * 1024,
    int(os.environ.get("AIVF_MIN_FREE_DISK_MB", "1024")) * 1024 * 1024,
)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _is_sqlite_busy(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message or "database table is locked" in message


def _run_db_write(operation, db_path=DB_PATH):
    """Run a SQLite write transaction with bounded retries for transient lock contention."""
    for attempt in range(_SQLITE_WRITE_RETRIES + 1):
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=10000")
                return operation(conn)
        except sqlite3.OperationalError as exc:
            if attempt >= _SQLITE_WRITE_RETRIES or not _is_sqlite_busy(exc):
                raise
            time.sleep(_SQLITE_RETRY_DELAY_SECONDS * (2 ** attempt))
    raise AssertionError("unreachable")


def init_db() -> None:
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                step TEXT NOT NULL DEFAULT 'waiting',
                params TEXT NOT NULL DEFAULT '{}',
                pkg_dir TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL,
                message TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_logs_job_id_id ON job_logs(job_id, id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                client_ip TEXT NOT NULL,
                ts REAL NOT NULL
            )
        """)
        if os.environ.get("AIVF_WORKER_PROCESS") != "1":
            conn.execute(
                "UPDATE jobs SET status='interrupted', step='interrupted', updated_at=CURRENT_TIMESTAMP "
                "WHERE status IN ('queued','running','cancelling')"
            )


def db_insert_job(job_id: str, topic: str, params: dict) -> None:
    def write(conn):
        conn.execute(
            "INSERT INTO jobs (id, topic, params) VALUES (?, ?, ?)",
            (job_id, topic, json.dumps(params)),
        )
    _run_db_write(write)


def db_update_job(job_id: str, **kwargs: Any) -> int:
    if not kwargs:
        return 0
    allowed = {"status", "step", "params", "pkg_dir", "error"}
    invalid = set(kwargs) - allowed
    if invalid:
        raise ValueError(f"Invalid job fields: {sorted(invalid)}")
    fields = ", ".join(f"{key}=?" for key in kwargs)

    def write(conn):
        cursor = conn.execute(
            f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            list(kwargs.values()) + [job_id],
        )
        return cursor.rowcount

    return _run_db_write(write)


def db_get_job(job_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def db_list_jobs() -> list:
    with get_db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()]


def db_append_log(job_id: str, level: str, message: str) -> None:
    def write(conn):
        conn.execute(
            "INSERT INTO job_logs (job_id, level, message) VALUES (?, ?, ?)",
            (job_id, level.upper(), str(message)[:10000]),
        )
    _run_db_write(write)


def db_logs_since(job_id: str, last_id: int = 0) -> list:
    with get_db() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id, created_at, level, message FROM job_logs WHERE job_id=? AND id>? ORDER BY id ASC",
            (job_id, last_id),
        ).fetchall()]


def get_settings() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    values = {}
    for row in rows:
        if row["key"] not in SETTINGS_SCHEMA:
            continue
        try:
            values[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed persisted setting: %s", row["key"])
    defaults = {
        "default_target_seconds": 45.0,
        "default_workflow": "default",
        "default_skip_qc": False,
        "default_use_groq": False,
        "default_v3_platform": "youtube_shorts",
        "default_v3_audience": "general short-form viewers",
        "default_v3_bpm": 120,
        "max_upload_mb": app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
        "max_concurrent_jobs": int(os.environ.get("AIVF_MAX_CONCURRENT_JOBS", "2")),
    }
    for key, value in values.items():
        try:
            defaults[key] = _validate_setting(key, value)
        except ValueError:
            logger.warning("Ignoring invalid persisted setting: %s", key)
    return defaults


def _validate_setting(key: str, value: Any) -> Any:
    if key not in SETTINGS_SCHEMA:
        raise ValueError(f"Unsupported setting: {key}")
    kind, minimum, maximum = SETTINGS_SCHEMA[key]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a boolean")
        return value
    if kind == "workflow":
        return normalize_workflow(str(value))
    if kind == "text":
        result = str(value).strip()
        if not minimum <= len(result) <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum} characters")
        return result
    if kind == "int":
        if isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if not minimum <= result <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        return result
    if kind == "float":
        return validate_target_seconds(value, key)
    raise ValueError(f"Unsupported setting type: {kind}")


def set_setting(key: str, value: Any) -> None:
    value = _validate_setting(key, value)

    def write(conn):
        conn.execute(
            "INSERT INTO settings (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    _run_db_write(write)


def set_runtime_default_secret(key: str, value: str) -> None:
    if key not in SECRET_PARAM_KEYS:
        raise ValueError(f"Unsupported runtime secret: {key}")
    _runtime_default_secrets[key] = str(value or "").strip()


def get_runtime_default_secrets() -> dict:
    return dict(_runtime_default_secrets)


def check_rate_limit(client_ip: str, max_requests: int = 10, window_seconds: int = 60) -> bool:
    now = time.time()
    cutoff = now - window_seconds

    def write(conn):
        conn.execute("DELETE FROM rate_limits WHERE ts<?", (cutoff,))
        count = conn.execute(
            "SELECT COUNT(*) FROM rate_limits WHERE client_ip=?", (client_ip,)
        ).fetchone()[0]
        if count >= max_requests:
            return False
        conn.execute("INSERT INTO rate_limits (client_ip,ts) VALUES (?,?)", (client_ip, now))
        return True

    return _run_db_write(write)


def _safe_topic_slug(topic: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", topic).strip("._-")[:60]
    return slug or "job"


def _safe_package_dir(topic: str, output_root: str) -> Path:
    root = Path(output_root).resolve()
    path = (root / f"{_safe_topic_slug(topic)}_{uuid.uuid4().hex[:10]}").resolve()
    if root not in path.parents:
        raise ValueError("Package path escaped output root")
    path.mkdir(parents=True, exist_ok=False)
    return path


def _probe_video(path: Path) -> bool:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return False
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_type,width,height,duration",
                "-show_entries", "format=duration", "-of", "json", str(path),
            ],
            capture_output=True, text=True, timeout=20, check=False,
        )
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        if result.returncode != 0 or not streams:
            return False
        stream = streams[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration = stream.get("duration") or (data.get("format") or {}).get("duration")
        duration = float(duration)
        return width > 0 and height > 0 and width <= 7680 and height <= 7680 and duration > 0 and duration <= 3600
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, json.JSONDecodeError):
        return False


def _save_and_validate_upload(upload, suffix: str) -> Path:
    max_bytes = int(app.config["MAX_CONTENT_LENGTH"])
    declared_size = getattr(upload, "content_length", None)
    if declared_size and declared_size > max_bytes:
        raise ValueError("Upload is too large")

    temp_fd, temp_name = tempfile.mkstemp(prefix=".upload-", suffix=suffix, dir=UPLOAD_FOLDER)
    os.close(temp_fd)
    temp_path = Path(temp_name)
    final_path = UPLOAD_FOLDER / f"{uuid.uuid4().hex}{suffix}"
    try:
        upload.save(temp_path)
        if temp_path.stat().st_size > max_bytes or not _probe_video(temp_path):
            raise ValueError("Upload is too large or is not a valid supported video stream")
        os.replace(temp_path, final_path)
        return final_path
    finally:
        temp_path.unlink(missing_ok=True)


def _redact_job(job: dict, include_logs: bool = False) -> dict:
    result = dict(job)
    try:
        params = json.loads(result.get("params") or "{}")
    except json.JSONDecodeError:
        params = {}
    for key in SECRET_PARAM_KEYS:
        params.pop(key, None)
    result["params"] = params
    if include_logs:
        result["logs"] = [
            {"id": row["id"], "time": row["created_at"], "level": row["level"], "msg": row["message"]}
            for row in db_logs_since(result["id"])
        ]
    return result


def _run_job_worker_impl(job_id: str, params: dict, secrets: dict, output_root: str, db_path: str) -> None:
    def update(**kwargs: Any) -> bool:
        allowed = {"status", "step", "params", "pkg_dir", "error"}
        if set(kwargs) - allowed:
            raise ValueError("Invalid worker update")
        fields = ", ".join(f"{key}=?" for key in kwargs)

        def write(conn):
            rowcount = conn.execute(
                f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND status NOT IN ('cancelling','cancelled','interrupted')",
                list(kwargs.values()) + [job_id],
            ).rowcount
            return rowcount > 0

        return _run_db_write(write, db_path)

    def log(level: str, message: str) -> None:
        def write(conn):
            conn.execute(
                "INSERT INTO job_logs (job_id, level, message) VALUES (?, ?, ?)",
                (job_id, level.upper(), str(message)[:10000]),
            )

        _run_db_write(write, db_path)

    try:
        if not update(status="running", step="Initializing"):
            return
        log("INFO", "→ Initializing")
        pkg_dir = _safe_package_dir(params["topic"], output_root)
        if params.get("workflow") == "v3":
            from ai_video_factory.v3_pipeline import run_v3_pipeline

            if not params.get("raw_video"):
                raise ValueError("V3 production requires a raw video")
            if not update(step="Running V3 Pipeline", pkg_dir=str(pkg_dir)):
                return
            log("INFO", "→ Running V3 Pipeline")
            result = run_v3_pipeline(
                params["raw_video"],
                params["topic"],
                str(pkg_dir),
                context=params.get("context", ""),
                target_seconds=params.get("target_seconds", 30.0),
                platform=params.get("platform", "youtube_shorts"),
                audience=params.get("audience", "general short-form viewers"),
                bpm=params.get("bpm", 120),
                edit_type=params.get("edit_type"),
                model_key=secrets.get("model_key"),
                skip_qc=params.get("skip_qc", False),
                music_path=params.get("music_path"),
                enable_ocr=params.get("enable_ocr", False),
                enable_object_detection=params.get("enable_object_detection", True),
                enable_diarization=params.get("enable_diarization", False),
                diarization_token=secrets.get("diarization_token"),
            )
            result_payload = {
                "errors": list(getattr(result, "errors", []) or []),
                "warnings": list(getattr(result, "warnings", []) or []),
                "artifacts": dict(getattr(result, "artifacts", {}) or {}),
                "final_video": getattr(result, "final_video", None),
            }
            with open(pkg_dir / "v3_job_result.json", "w", encoding="utf-8") as handle:
                json.dump(result_payload, handle, indent=2, ensure_ascii=False)
            if result_payload["errors"]:
                message = "; ".join(str(error) for error in result_payload["errors"])
                if update(status="error", step="failed", error=message, pkg_dir=str(pkg_dir)):
                    log("ERROR", message)
            else:
                if update(status="done", step="Complete (V3)", pkg_dir=str(pkg_dir)):
                    log("INFO", "V3 job complete!")
                    for warning in result_payload["warnings"]:
                        log("WARNING", str(warning))
            return

        from ai_video_factory.pipeline import PipelineContext, build_director_pipeline
        ctx = PipelineContext(
            topic=params["topic"],
            raw_video=params.get("raw_video"),
            target_seconds=params.get("target_seconds", 45.0),
            skip_qc=params.get("skip_qc", False),
            use_groq=params.get("use_groq", False),
            model_key=secrets.get("model_key"),
            groq_key=secrets.get("groq_key"),
        )
        ctx.package_dir = str(pkg_dir)
        if not update(step="Running Pipeline", pkg_dir=str(pkg_dir)):
            return
        log("INFO", "→ Running Pipeline")
        ctx = build_director_pipeline().run(ctx)
        if ctx.errors:
            message = "; ".join(str(error) for error in ctx.errors)
            if update(status="error", step="failed", error=message, pkg_dir=str(pkg_dir)):
                log("ERROR", message)
        else:
            if update(status="done", step="Complete", pkg_dir=str(pkg_dir)):
                log("INFO", "Job complete!")
    except Exception as exc:
        try:
            if update(status="error", step="failed", error=str(exc)):
                log("ERROR", f"Job failed: {exc}")
        except Exception:
            logger.exception("Could not record worker failure for %s", job_id)


def _invalidate_package_cache() -> None:
    global _package_cache
    with _package_cache_lock:
        _package_cache = None


def _watch_job_process(job_id: str, process: multiprocessing.Process) -> None:
    process.join()
    exitcode = process.exitcode
    with _active_processes_lock:
        _active_processes.pop(job_id, None)
        _runtime_secrets.pop(job_id, None)
    _invalidate_package_cache()

    # A hard worker crash can bypass the worker's exception handler entirely.
    # Never leave a job permanently stuck in RUNNING/CANCELLING.
    try:
        row = db_get_job(job_id)
        if row and row.get("status") in {"queued", "running", "cancelling"}:
            reason = (
                f"Worker process exited unexpectedly with code {exitcode}"
                if exitcode not in (0, None)
                else "Worker process exited before reaching a terminal job state"
            )
            db_update_job(job_id, status="interrupted", step="interrupted", error=reason)
            db_append_log(job_id, "ERROR", reason)
    except Exception:
        logger.exception("Could not reconcile worker exit for %s", job_id)


def _running_count() -> int:
    with _active_processes_lock:
        dead = [job_id for job_id, process in _active_processes.items() if not process.is_alive()]
        for job_id in dead:
            _active_processes.pop(job_id, None)
            _runtime_secrets.pop(job_id, None)
        return sum(1 for process in _active_processes.values() if process.is_alive())


def _start_job(job_id: str, params: dict, secrets: dict) -> bool:
    with _active_processes_lock:
        if _running_count() >= max(1, int(get_settings()["max_concurrent_jobs"])):
            return False
        from dashboard_worker import run_job
        ctx = multiprocessing.get_context("spawn")
        process = ctx.Process(
            target=run_job,
            args=(job_id, params, secrets, str(OUTPUT_FOLDER), str(DB_PATH)),
            daemon=False,
        )
        process.start()
        _active_processes[job_id] = process
        threading.Thread(
            target=_watch_job_process,
            args=(job_id, process),
            name=f"aivf-reaper-{job_id}",
            daemon=True,
        ).start()
        return True


def _resolve_package(name: str) -> Optional[Path]:
    root = OUTPUT_FOLDER.resolve()
    candidate = (OUTPUT_FOLDER / name).resolve()
    if root not in candidate.parents or not candidate.is_dir():
        return None
    return candidate


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.path.startswith("/api/jobs"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(RequestEntityTooLarge)
def too_large(_error):
    return jsonify({"error": "Upload exceeds configured size limit"}), 413


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Settings payload must be a JSON object"}), 400
        try:
            for key, value in data.items():
                set_setting(key, value)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    return jsonify(get_settings())


@app.route("/api/jobs", methods=["POST"])
def create_job():
    client_ip = request.remote_addr or "unknown"
    if not check_rate_limit(client_ip):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    topic = str(data.get("topic", "")).strip()
    if not 2 <= len(topic) <= 500:
        return jsonify({"error": "Topic must be between 2 and 500 characters"}), 400
    settings_data = get_settings()
    try:
        workflow = normalize_workflow(data.get("workflow", settings_data["default_workflow"]))
        if workflow == "v3":
            target_seconds = validate_v3_target_seconds(
                data.get("target_seconds", settings_data.get("default_target_seconds", 45.0))
            )
        else:
            target_seconds = validate_target_seconds(
                data.get("target_seconds", settings_data["default_target_seconds"])
            )
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        with get_db() as conn:
            queued = int(conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='queued'"
            ).fetchone()[0])
        if queued >= _MAX_QUEUED_JOBS:
            return jsonify({"error": "Queue capacity reached. Retry later."}), 429
    except sqlite3.Error:
        return jsonify({"error": "Job queue is temporarily unavailable"}), 503

    params = {
        "topic": topic,
        "target_seconds": target_seconds,
        "workflow": workflow,
        "use_groq": str(data.get("use_groq", "")).lower() in {"1", "true", "on", "yes"},
        "skip_qc": (
            os.environ.get("AIVF_ALLOW_SKIP_QC", "0") == "1"
            and str(data.get("skip_qc", "")).lower() in {"1", "true", "on", "yes"}
        ),
    }
    if workflow == "v3":
        platform = str(data.get("platform", settings_data.get("default_v3_platform", "youtube_shorts"))).strip().lower()
        audience = str(data.get("audience", settings_data.get("default_v3_audience", "general short-form viewers"))).strip()
        edit_type = str(data.get("edit_type", "")).strip() or None
        try:
            bpm = int(data.get("bpm", settings_data.get("default_v3_bpm", 120)))
        except (TypeError, ValueError):
            return jsonify({"error": "bpm must be an integer between 40 and 240"}), 400
        allowed_platforms = {"youtube_shorts", "tiktok", "instagram_reels", "square", "youtube"}
        allowed_edit_types = {"Emotional", "Motivational", "Nostalgic", "Funny", "Dramatic", "Documentary", "Sigma", "Character Analysis", "Tribute", "Storytelling"}
        if platform not in allowed_platforms:
            return jsonify({"error": "Unsupported V3 platform"}), 400
        if not audience or len(audience) > 500:
            return jsonify({"error": "V3 audience must be between 1 and 500 characters"}), 400
        if not 40 <= bpm <= 240:
            return jsonify({"error": "V3 bpm must be between 40 and 240"}), 400
        if edit_type and edit_type not in allowed_edit_types:
            return jsonify({"error": "Unsupported V3 edit type"}), 400
        params.update({
            "platform": platform,
            "audience": audience,
            "bpm": bpm,
            "edit_type": edit_type,
            "context": str(data.get("context", "")).strip()[:2000],
            "enable_ocr": str(data.get("enable_ocr", "")).lower() in {"1", "true", "on", "yes"},
            "enable_object_detection": str(data.get("enable_object_detection", "true")).lower() in {"1", "true", "on", "yes"},
            "enable_diarization": str(data.get("enable_diarization", "")).lower() in {"1", "true", "on", "yes"},
        })
    secrets = get_runtime_default_secrets()
    secrets.update({key: str(data.get(key, "")).strip() for key in SECRET_PARAM_KEYS if data.get(key)})
    if workflow == "v3" and params.get("enable_diarization") and not secrets.get("diarization_token"):
        return jsonify({"error": "Speaker diarization is enabled but no diarization token is configured"}), 400
    if workflow == "v3" and not (request.files.get("raw_video") and request.files.get("raw_video").filename):
        return jsonify({"error": "V3 production requires a raw video upload"}), 400

    upload = request.files.get("raw_video")
    try:
        usage = shutil.disk_usage(UPLOAD_FOLDER)
        if usage.free < _MIN_FREE_DISK_BYTES:
            return jsonify({"error": "Server disk space is too low"}), 503
    except OSError:
        return jsonify({"error": "Server disk space could not be checked"}), 503

    if upload and upload.filename:
        filename = secure_filename(upload.filename)
        suffix = Path(filename).suffix.lower()
        if not filename or suffix not in ALLOWED_EXTENSIONS:
            return jsonify({"error": "Unsupported video file type"}), 400
        try:
            upload_path = _save_and_validate_upload(upload, suffix)
            remaining = shutil.disk_usage(UPLOAD_FOLDER).free
            if remaining < _MIN_FREE_DISK_BYTES:
                upload_path.unlink(missing_ok=True)
                return jsonify({"error": "Server disk space is too low after upload"}), 503
        except (OSError, ValueError):
            return jsonify({"error": "Upload is too large or is not a valid supported video stream"}), 400
        params["raw_video"] = str(upload_path)

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    db_insert_job(job_id, topic, params)
    _runtime_secrets[job_id] = secrets
    _start_job(job_id, params, secrets)
    return jsonify({"job_id": job_id, "status": "queued"}), 202


@app.route("/api/jobs", methods=["GET"])
def list_jobs():
    return jsonify([_redact_job(job) for job in db_list_jobs()])


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    job = db_get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(_redact_job(job, include_logs=True))


@app.route("/api/jobs/<job_id>/status")
def get_job_status(job_id):
    job = db_get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"id": job_id, "status": job["status"], "step": job["step"], "error": job["error"]})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    from dashboard_compat import cancel_process
    return cancel_process(job_id)


@app.route("/api/jobs/<job_id>/logs")
@app.route("/api/jobs/<job_id>/logs/stream")
def job_logs_stream(job_id):
    def stream():
        last_id = 0
        while True:
            job = db_get_job(job_id)
            if not job:
                yield "data: " + json.dumps({"level": "ERROR", "msg": "Job not found"}) + "\n\n"
                return
            for row in db_logs_since(job_id, last_id):
                last_id = row["id"]
                yield "data: " + json.dumps({"time": row["created_at"], "level": row["level"], "msg": row["message"]}) + "\n\n"
            if job["status"] in TERMINAL_STATUSES:
                return
            yield ": heartbeat\n\n"
            time.sleep(0.5)
    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/packages")
def list_packages():
    global _package_cache
    now = time.monotonic()
    with _package_cache_lock:
        if _package_cache and now - _package_cache[0] < _PACKAGE_CACHE_TTL:
            return jsonify(_package_cache[1])

    packages = []
    try:
        package_paths = [p for p in OUTPUT_FOLDER.iterdir() if p.is_dir()]
    except OSError:
        package_paths = []
    package_paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for pkg_path in package_paths:
        try:
            thumbnail_name = next(
                (name for name in ("thumbnail.png", "thumbnail_vertical.png") if (pkg_path / name).exists()),
                None,
            )
            script = pkg_path / "script.txt"
            preview = script.read_text(encoding="utf-8", errors="replace")[:200] if script.exists() else ""
            created = datetime.fromtimestamp(pkg_path.stat().st_ctime).strftime("%Y-%m-%d %H:%M")
            readiness_state = None
            readiness_path = pkg_path / "v3_readiness.json"
            if readiness_path.exists():
                try:
                    readiness_state = json.loads(readiness_path.read_text(encoding="utf-8")).get("state")
                except (OSError, ValueError, TypeError):
                    readiness_state = None
            packages.append({
                "name": pkg_path.name,
                "created": created,
                "thumbnail": f"/api/packages/{pkg_path.name}/file/{thumbnail_name}" if thumbnail_name else None,
                "script_preview": preview,
                "has_video": any(
                    (pkg_path / name).exists()
                    for name in ("final_short.mp4", "final_with_music.mp4", "final_short_vo.mp4", "final.v3.mp4")
                ),
                "v3_readiness": readiness_state,
            })
        except OSError:
            continue

    with _package_cache_lock:
        _package_cache = (time.monotonic(), packages)
    return jsonify(packages)


@app.route("/api/packages/<name>/file/<path:filename>")
def package_file(name, filename):
    pkg_dir = _resolve_package(name)
    if not pkg_dir:
        abort(404)
    return send_from_directory(pkg_dir, filename)


@app.route("/api/health")
def health():
    usage = shutil.disk_usage(UPLOAD_FOLDER)
    return jsonify({
        "status": "ok",
        "disk_free_mb": round(usage.free / (1024 * 1024), 1),
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "ffprobe_available": shutil.which("ffprobe") is not None,
        "active_jobs": _running_count(),
        "max_content_length_mb": app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
    })


init_db()

if __name__ == "__main__":
    from dashboard_auth import configure_dashboard_auth
    from dashboard_compat import register_dashboard_compat
    configure_dashboard_auth(app)
    register_dashboard_compat(app)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
