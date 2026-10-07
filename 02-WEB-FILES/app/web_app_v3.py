"""AI Video Factory production dashboard.

Single-host architecture: Flask + SQLite + one spawned process per active job.
Secrets stay in process memory and are never persisted in job records.
"""
import json
import importlib.util
import logging
import multiprocessing
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from flask import Flask, Response, abort, jsonify, render_template, request, send_file, send_from_directory, session
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename

from ai_video_factory.validation import normalize_workflow, validate_target_seconds, validate_v3_target_seconds
from ai_video_factory.render_engine import run_ffprobe
from ai_video_factory.production_guardrails import GuardrailError, sha256_file
from ai_video_factory.media_limits import DEFAULT_MEDIA_LIMITS, estimate_resource_budget, validate_input_file
from ai_video_factory.error_codes import classify_exception
from ai_video_factory.observability_metrics import GLOBAL_METRICS
from ai_video_factory.runtime_capabilities import capabilities
from ai_video_factory.runtime_config import runtime_config
from ai_video_factory.retry_policy import idempotency_key as request_idempotency_hash
from app.job_service import build_job_params

APP_DIR = Path(__file__).resolve().parent
SOURCE_WEB_DIR = APP_DIR.parent if (APP_DIR.parent / "templates").is_dir() else None
INSTALLED_WEB_DIR = Path(sys.prefix) / "share" / "ai-video-factory"
BASE_DIR = SOURCE_WEB_DIR or INSTALLED_WEB_DIR
if not (BASE_DIR / "templates").is_dir() or not (BASE_DIR / "static").is_dir():
    BASE_DIR = APP_DIR

STATE_DIR = Path(os.environ.get("AIVF_STATE_DIR", BASE_DIR / "state")).resolve()
UPLOAD_FOLDER = Path(os.environ.get("AIVF_UPLOAD_DIR", BASE_DIR / "uploads")).resolve()
OUTPUT_FOLDER = Path(os.environ.get("AIVF_OUTPUT_DIR", BASE_DIR / "output")).resolve()
RUNTIME_CONFIG = runtime_config()
DB_PATH = STATE_DIR / "jobs.db"
for directory in (STATE_DIR, UPLOAD_FOLDER, OUTPUT_FOLDER):
    directory.mkdir(parents=True, exist_ok=True)

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
configured_secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
if not configured_secret:
    configured_secret = os.environ.get("AIVF_DASHBOARD_SECRET_KEY", "").strip()
if not configured_secret:
    # The WSGI/auth bootstrap replaces this with a stable deployment key.
    # A process-local fallback keeps direct imports and test clients session-capable.
    configured_secret = secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=RUNTIME_CONFIG.max_upload_mb * 1024 * 1024,
    SECRET_KEY=configured_secret,
    AIVF_STATE_DIR=str(STATE_DIR),
)

logger = logging.getLogger("web_app_v3")

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
SECRET_PARAM_KEYS = {"groq_key", "model_key", "elevenlabs_key", "diarization_token"}
INTERNAL_PARAM_KEYS = {"_principal", "_retry_secret_keys", "raw_video", "pkg_dir", "_media_summary", "_resource_budget"}
RUNTIME_SECRET_TTL_SECONDS = RUNTIME_CONFIG.retry_secret_ttl_seconds
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
_runtime_secret_expiry: Dict[str, float] = {}
_runtime_default_secrets: Dict[str, str] = {key: "" for key in SECRET_PARAM_KEYS}
_active_processes: Dict[str, multiprocessing.Process] = {}
_active_processes_lock = threading.RLock()
_queue_pump_lock = threading.RLock()
_QUEUE_PUMP_INTERVAL_SECONDS = max(0.5, float(os.environ.get("AIVF_QUEUE_PUMP_INTERVAL_SECONDS", "2.0")))
_package_cache_lock = threading.RLock()
_package_cache: Optional[tuple[float, list]] = None
_PACKAGE_CACHE_TTL = 2.0
_SQLITE_WRITE_RETRIES = 3
_SQLITE_RETRY_DELAY_SECONDS = 0.05
_MAX_QUEUED_JOBS = RUNTIME_CONFIG.max_queued_jobs
_MIN_FREE_DISK_BYTES = DEFAULT_MEDIA_LIMITS.min_free_disk_bytes


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
    """Validate or initialize the database at application bootstrap."""
    from dashboard_store import DashboardStore

    store = DashboardStore(DB_PATH)
    environment = os.environ.get("AIVF_ENV", "development").strip().lower()
    auto_migrate_raw = os.environ.get(
        "AIVF_AUTO_MIGRATE",
        "0" if environment == "production" else "1",
    ).strip()
    auto_migrate = auto_migrate_raw == "1"

    if auto_migrate:
        store.ensure_indexes()
        if os.environ.get("AIVF_WORKER_PROCESS") != "1":
            store.recover_nonterminal_jobs()
        return

    # Production deployments must run the migration command before starting
    # the application. Startup is verification-only so two processes cannot
    # race to mutate the schema.
    store.verify_schema()
    if os.environ.get("AIVF_WORKER_PROCESS") != "1":
        store.recover_nonterminal_jobs()

def db_insert_job(job_id: str, topic: str, params: dict) -> None:
    def write(conn):
        conn.execute(
            "INSERT INTO jobs (id, topic, params) VALUES (?, ?, ?)",
            (job_id, topic, json.dumps(params)),
        )
    _run_db_write(write)


def db_update_job(job_id: str, **kwargs: Any) -> int:
    """Legacy-compatible status update routed through the durable lifecycle rules."""
    if not kwargs:
        return 0
    allowed = {"status", "step", "params", "pkg_dir", "error"}
    invalid = set(kwargs) - allowed
    if invalid:
        raise ValueError(f"Invalid job fields: {sorted(invalid)}")

    def write(conn):
        row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return 0
        current = str(row["status"])
        target = str(kwargs.get("status", current))
        if current == "queued" and target == "done":
            conn.execute(
                "UPDATE jobs SET status='running', step='starting', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",
                (job_id,),
            )
            current = "running"
        if target != current:
            from ai_video_factory.job_state import validate_transition
            validate_transition(current, target)
        fields = ", ".join(f"{key}=?" for key in kwargs)
        return conn.execute(
            f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            list(kwargs.values()) + [job_id],
        ).rowcount

    return _run_db_write(write)


def db_claim_job(job_id: str) -> bool:
    """Atomically transition a queued job to running before worker creation."""
    current = db_get_job(job_id)
    if not current:
        return False
    from ai_video_factory.job_state import validate_transition
    validate_transition(str(current["status"]), "running")
    def write(conn):
        return conn.execute(
            "UPDATE jobs SET status='running', step='starting', error=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status='queued'",
            (job_id,),
        ).rowcount == 1
    return bool(_run_db_write(write))


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
    try:
        platform = defaults["default_v3_platform"]
        max_seconds = {
            "youtube_shorts": 60.0,
            "tiktok": 180.0,
            "instagram_reels": 90.0,
            "square": 90.0,
            "youtube": 180.0,
        }[platform]
        if float(defaults["default_target_seconds"]) > max_seconds:
            defaults["default_target_seconds"] = min(45.0, max_seconds)
    except (KeyError, TypeError, ValueError):
        defaults["default_v3_platform"] = "youtube_shorts"
        defaults["default_target_seconds"] = 45.0
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
        if key == "default_v3_platform":
            allowed = {"youtube_shorts", "tiktok", "instagram_reels", "square", "youtube"}
            if result.lower() not in allowed:
                raise ValueError("default_v3_platform is unsupported")
            return result.lower()
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


def _save_and_validate_upload(upload, suffix: str) -> tuple[Path, dict[str, Any]]:
    max_bytes = min(int(app.config["MAX_CONTENT_LENGTH"]), DEFAULT_MEDIA_LIMITS.max_input_bytes)
    declared_size = getattr(upload, "content_length", None)
    if declared_size and declared_size > max_bytes:
        raise ValueError("Upload is too large")

    temp_fd, temp_name = tempfile.mkstemp(prefix=".upload-", suffix=suffix, dir=UPLOAD_FOLDER)
    os.close(temp_fd)
    temp_path = Path(temp_name)
    final_path = UPLOAD_FOLDER / f"{uuid.uuid4().hex}{suffix}"
    try:
        upload.save(temp_path)
        if temp_path.stat().st_size > max_bytes:
            raise ValueError("Upload is too large")
        summary = validate_input_file(temp_path, suffix=suffix, limits=DEFAULT_MEDIA_LIMITS)
        os.replace(temp_path, final_path)
        return final_path, summary
    finally:
        temp_path.unlink(missing_ok=True)

def _probe_video(path: Path) -> bool:
    try:
        result = run_ffprobe([
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type,width,height,duration",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ], timeout=30)
        if result.returncode != 0:
            return False
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        if not streams:
            return False
        stream = streams[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        duration_raw = stream.get("duration") or (payload.get("format") or {}).get("duration")
        duration = float(duration_raw)
        return (
            width > 0
            and height > 0
            and width <= 7680
            and height <= 7680
            and duration > 0
            and duration <= 3600
        )
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def _runtime_capabilities() -> dict[str, bool]:
    detected = capabilities()
    vision = detected["vision"].available
    from ai_video_factory.asset_manager import RUNTIME_ASSETS, verify_runtime_asset
    configured_model = Path(
        os.environ.get(
            "EDIT_FACTORY_MOBILENET_MODEL",
            ".models/mobilenet_ssd/mobilenet.caffemodel",
        )
    )
    configured_config = Path(
        os.environ.get(
            "EDIT_FACTORY_MOBILENET_CONFIG",
            ".models/mobilenet_ssd/deploy.prototxt",
        )
    )
    model_spec = next(
        (item for item in RUNTIME_ASSETS if item.name == "mobilenet_ssd_weights"),
        None,
    )
    config_spec = next(
        (item for item in RUNTIME_ASSETS if item.name == "mobilenet_ssd_config"),
        None,
    )
    object_detection = bool(
        vision
        and model_spec is not None
        and config_spec is not None
        and configured_model == Path(model_spec.relative_path)
        and configured_config == Path(config_spec.relative_path)
        and verify_runtime_asset(model_spec)
        and verify_runtime_asset(config_spec)
    )
    return {
        "ocr": detected["ocr"].available,
        "object_detection": object_detection,
        "diarization": detected["diarization"].available,
    }


def _retain_runtime_secrets_for_retry(job_id: str) -> None:
    if job_id in _runtime_secrets:
        _runtime_secret_expiry[job_id] = time.monotonic() + RUNTIME_SECRET_TTL_SECONDS


def _cleanup_runtime_secrets() -> None:
    now = time.monotonic()
    expired = [job_id for job_id, deadline in _runtime_secret_expiry.items() if deadline <= now]
    for job_id in expired:
        _runtime_secret_expiry.pop(job_id, None)
        _runtime_secrets.pop(job_id, None)


def _redact_job(job: dict, include_logs: bool = False) -> dict:
    result = dict(job)
    try:
        params = json.loads(result.get("params") or "{}")
    except json.JSONDecodeError:
        params = {}
    for key in SECRET_PARAM_KEYS | INTERNAL_PARAM_KEYS:
        params.pop(key, None)
    result.pop("pkg_dir", None)
    package_value = str(job.get("pkg_dir") or "").strip()
    if package_value:
        try:
            result["package_name"] = Path(package_value).name
        except OSError:
            result["package_name"] = None
    result["params"] = params
    if include_logs:
        result["logs"] = [
            {"id": row["id"], "time": row["created_at"], "level": row["level"], "msg": row["message"]}
            for row in db_logs_since(result["id"])
        ]
    return result


def _run_job_worker_impl(
    job_id: str,
    params: dict,
    secrets: dict,
    output_root: str,
    db_path: str,
    skip_stages: Optional[list[str]] = None,
    *,
    attempt_id: str | None = None,
    lease_token: str | None = None,
    worker_id: str | None = None,
    workspace_dir: str | None = None,
    publish_dir: str | None = None,
) -> None:
    resource_monitor = None
    resource_report_written = False
    store = globals().get("dashboard_store")
    if store is None:
        from dashboard_store import DashboardStore
        store = DashboardStore(db_path)

    def owned() -> bool:
        if not (attempt_id and lease_token):
            return True
        return bool(store.is_attempt_owner(job_id, attempt_id, lease_token))

    def _write_resource_report(package_dir: Optional[Path]) -> None:
        nonlocal resource_report_written
        if resource_report_written or resource_monitor is None or package_dir is None:
            return
        try:
            report = resource_monitor.stop_monitoring()
            target = package_dir / "resource_usage.json"
            temporary = package_dir / ".resource_usage.json.partial"
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2, sort_keys=True, ensure_ascii=False)
                handle.write("\n")
            os.replace(temporary, target)
        except Exception as exc:
            logger.warning("Resource telemetry write skipped for %s: %s", job_id, exc)
        finally:
            resource_report_written = True

    def update(**kwargs: Any) -> bool:
        if not owned():
            return False
        if attempt_id and lease_token:
            return bool(store.update_job_if_owned(job_id, attempt_id, lease_token, **kwargs))
        allowed = {"status", "step", "params", "pkg_dir", "error"}
        if set(kwargs) - allowed:
            raise ValueError("Invalid worker update")
        fields = ", ".join(f"{key}=?" for key in kwargs)
        def write(conn):
            return conn.execute(
                f"UPDATE jobs SET {fields}, updated_at=CURRENT_TIMESTAMP WHERE id=? "
                "AND status NOT IN ('cancelling','cancelled','interrupted')",
                list(kwargs.values()) + [job_id],
            ).rowcount > 0
        return bool(_run_db_write(write, db_path))

    def log(level: str, message: str) -> None:
        if attempt_id and lease_token:
            if owned():
                store.append_log(job_id, level, message)
            return
        def write(conn):
            conn.execute(
                "INSERT INTO job_logs (job_id, level, message) VALUES (?, ?, ?)",
                (job_id, level.upper(), str(message)[:10000]),
            )
        _run_db_write(write, db_path)

    def publish_workspace(source: Path, target: Path, final_video: Any) -> Any:
        if attempt_id and lease_token and not owned():
            raise RuntimeError("worker attempt lost ownership before artifact publication")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise RuntimeError("publish destination already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        if final_video:
            try:
                relative = Path(str(final_video)).resolve().relative_to(source.resolve())
                return str((target / relative).resolve())
            except (OSError, RuntimeError, ValueError):
                return final_video
        return final_video

    try:
        from ai_video_factory.resource_metrics import ResourceMonitor
        from ai_video_factory.media_limits import DEFAULT_MEDIA_LIMITS

        raw_interval = os.environ.get("AIVF_RESOURCE_SAMPLE_SECONDS", "1.0")
        try:
            interval_seconds = float(raw_interval)
        except (TypeError, ValueError) as exc:
            raise ValueError("AIVF_RESOURCE_SAMPLE_SECONDS must be numeric") from exc
        if interval_seconds != interval_seconds or interval_seconds <= 0:
            raise ValueError("AIVF_RESOURCE_SAMPLE_SECONDS must be finite and > 0")
        resource_monitor = ResourceMonitor(interval_seconds=max(0.25, interval_seconds))
        resource_monitor.start_monitoring()

        if not update(status="running", step="Initializing"):
            return
        log("INFO", "→ Initializing")

        if workspace_dir:
            pkg_dir = Path(workspace_dir).resolve()
            pkg_dir.mkdir(parents=True, exist_ok=True)
        else:
            pkg_dir = _safe_package_dir(params["topic"], output_root)

        if params.get("workflow") == "v3":
            from ai_video_factory.v3_pipeline import run_v3_pipeline
            if not params.get("raw_video"):
                raise ValueError("V3 production requires a raw video")
            if not update(step="Running V3 Pipeline", pkg_dir=str(pkg_dir)):
                return
            log("INFO", "→ Running V3 Pipeline")
            result = run_v3_pipeline(
                params["raw_video"], params["topic"], str(pkg_dir),
                context=params.get("context", ""),
                target_seconds=params.get("target_seconds", 30.0),
                platform=params.get("platform", "youtube_shorts"),
                audience=params.get("audience", "general short-form viewers"),
                bpm=params.get("bpm", 120), edit_type=params.get("edit_type"),
                model_key=secrets.get("model_key"), skip_qc=params.get("skip_qc", False),
                music_path=params.get("music_path"), enable_ocr=params.get("enable_ocr", False),
                enable_object_detection=params.get("enable_object_detection", True),
                enable_diarization=params.get("enable_diarization", False),
                diarization_token=secrets.get("diarization_token"),
                source_metadata=params.get("source_metadata"),
                allow_unsupported_critical_evidence=bool(params.get("allow_unsupported_critical_evidence", False)),
            )
            result_payload = {
                "errors": list(getattr(result, "errors", []) or []),
                "warnings": list(getattr(result, "warnings", []) or []),
                "artifacts": dict(getattr(result, "artifacts", {}) or {}),
                "final_video": getattr(result, "final_video", None),
            }
            with open(pkg_dir / "v3_job_result.json", "w", encoding="utf-8") as handle:
                json.dump(result_payload, handle, indent=2, ensure_ascii=False)
            if result_payload["errors"] or not _artifact_is_valid(result_payload.get("final_video")):
                if not result_payload["errors"]:
                    result_payload["errors"].append("V3 final artifact failed media validation")
                message = "; ".join(str(error) for error in result_payload["errors"])
                error = classify_exception(RuntimeError(message))
                update(
                    status="error",
                    step="failed",
                    error=message,
                    error_code=error.code.value,
                    pkg_dir=str(pkg_dir),
                )
                if owned():
                    log("ERROR", f"[{error.code.value}] {message}")
            else:
                _write_resource_report(pkg_dir)
                if attempt_id and lease_token and publish_dir:
                    published = publish_workspace(pkg_dir, Path(publish_dir), result_payload.get("final_video"))
                    result_payload["final_video"] = published
                    try:
                        store.record_artifact(
                            job_id,
                            "final_video",
                            str(published),
                            attempt_id=attempt_id,
                            sha256=sha256_file(str(published)),
                            size_bytes=Path(str(published)).stat().st_size,
                        )
                    except Exception as exc:
                        raise RuntimeError(f"artifact registration failed: {exc}") from exc

                    published_dir = Path(publish_dir)
                    from ai_video_factory.production_assurance import build_artifact_manifest
                    previous_manifest_path = published_dir / "artifact_manifest.json"
                    try:
                        previous_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
                    except (OSError, TypeError, ValueError, json.JSONDecodeError):
                        previous_manifest = {}
                    required_files = previous_manifest.get("required_files") or []
                    final_manifest = build_artifact_manifest(
                        published_dir,
                        required_files=required_files,
                        include_hashes=True,
                    )
                    temporary_manifest = published_dir / ".artifact_manifest.json.partial"
                    temporary_manifest.write_text(
                        json.dumps(final_manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    os.replace(temporary_manifest, previous_manifest_path)

                    with open(published_dir / "v3_job_result.json", "w", encoding="utf-8") as handle:
                        json.dump(result_payload, handle, indent=2, ensure_ascii=False)
                if update(status="done", step="Complete (V3)", pkg_dir=str(publish_dir or pkg_dir)):
                    log("INFO", "V3 job complete!")
                    for warning in result_payload["warnings"]:
                        log("WARNING", str(warning))
            return

        from ai_video_factory.pipeline import PipelineContext, build_director_pipeline
        ctx = PipelineContext(
            topic=params["topic"], raw_video=params.get("raw_video"),
            target_seconds=params.get("target_seconds", 45.0), skip_qc=params.get("skip_qc", False),
            use_groq=params.get("use_groq", False), model_key=secrets.get("model_key"),
            groq_key=secrets.get("groq_key"),
        )
        ctx.package_dir = str(pkg_dir)
        if not update(step="Running Pipeline", pkg_dir=str(pkg_dir)):
            return
        log("INFO", "→ Running Pipeline")
        ctx = build_director_pipeline(skip_stages=skip_stages).run(ctx)
        if ctx.errors or not _artifact_is_valid(ctx.final_video):
            if not ctx.errors:
                ctx.errors.append("Final artifact failed media validation")
            message = "; ".join(str(error) for error in ctx.errors)
            error = classify_exception(RuntimeError(message))
            update(
                status="error",
                step="failed",
                error=message,
                error_code=error.code.value,
                pkg_dir=str(pkg_dir),
            )
            if owned():
                log("ERROR", f"[{error.code.value}] {message}")
        else:
            _write_resource_report(pkg_dir)
            published_video = ctx.final_video
            if attempt_id and lease_token and publish_dir:
                published_video = publish_workspace(pkg_dir, Path(publish_dir), ctx.final_video)
            try:
                store.record_artifact(
                    job_id,
                    "final_video",
                    str(published_video),
                    attempt_id=attempt_id,
                    sha256=sha256_file(str(published_video)),
                    size_bytes=Path(str(published_video)).stat().st_size,
                )
            except Exception as exc:
                raise RuntimeError(f"artifact registration failed: {exc}") from exc
            if update(status="done", step="Complete", pkg_dir=str(publish_dir or pkg_dir)):
                log("INFO", "Job complete!")
    except Exception as exc:
        try:
            error = classify_exception(exc)
            if update(status="error", step="failed", error=error.message, error_code=error.code.value):
                log("ERROR", f"[{error.code.value}] Job failed: {error.message}")
        except Exception:
            logger.exception("Could not record worker failure for %s", job_id)
    finally:
        if resource_monitor is not None and not resource_report_written:
            try:
                package_dir = locals().get("pkg_dir")
                _write_resource_report(package_dir if isinstance(package_dir, Path) else None)
            except Exception:
                logger.exception("Could not finalize resource telemetry for %s", job_id)
        try:
            final_row = store.get_job(job_id)
            if final_row and (
                not (attempt_id and lease_token)
                or store.has_attempt_ownership(job_id, attempt_id, lease_token)
            ):
                final_status = str(final_row.get("status") or "")
                if final_status == "done":
                    GLOBAL_METRICS.increment("jobs_completed_total")
                elif final_status == "error":
                    GLOBAL_METRICS.increment("jobs_failed_total")
                elif final_status == "interrupted":
                    GLOBAL_METRICS.increment("job_recovery_total")
                started_at = final_row.get("started_at")
                if started_at:
                    try:
                        from datetime import datetime, timezone
                        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                        if started.tzinfo is None:
                            started = started.replace(tzinfo=timezone.utc)
                        GLOBAL_METRICS.observe_ms(
                            "job_duration",
                            max(0.0, (datetime.now(timezone.utc) - started).total_seconds() * 1000.0),
                        )
                    except (TypeError, ValueError, OverflowError):
                        pass
        except Exception:
            logger.debug("Unable to record final job metrics", exc_info=True)


def _artifact_is_valid(final_video: Any) -> bool:
    """Require a probeable video artifact before the durable job reaches DONE."""
    if not final_video:
        return False
    try:
        from ai_video_factory.render_engine import validate_media_output
        validate_media_output(str(final_video), require_video=True, require_audio=False)
        return True
    except (OSError, RuntimeError, ValueError):
        return False

def _invalidate_package_cache() -> None:
    global _package_cache
    with _package_cache_lock:
        _package_cache = None


def _watch_job_process(
    job_id: str,
    process: multiprocessing.Process,
    attempt_id: str | None = None,
    lease_token: str | None = None,
    worker_id: str | None = None,
) -> None:
    from ai_video_factory.media_limits import DEFAULT_MEDIA_LIMITS
    from ai_video_factory.production_guardrails import terminate_process_tree

    try:
        process.join(timeout=max(30, int(DEFAULT_MEDIA_LIMITS.max_job_seconds) + 30))
        if process.is_alive():
            terminate_process_tree(process, grace_seconds=2.0)
            process.join(timeout=5)
            exitcode = process.exitcode
            reason = (
                f"Worker exceeded hard job runtime limit of {DEFAULT_MEDIA_LIMITS.max_job_seconds}s"
            )
        else:
            exitcode = process.exitcode
            reason = (
                f"Worker process exited unexpectedly with code {exitcode}"
                if exitcode not in (0, None)
                else "Worker process exited before reaching a terminal job state"
            )
            if exitcode not in (0, None):
                try:
                    from ai_video_factory.observability_metrics import GLOBAL_METRICS
                    GLOBAL_METRICS.increment("worker_crashes_total")
                    GLOBAL_METRICS.increment("job_recovery_total")
                except Exception:
                    pass

        try:
            store = globals().get("dashboard_store")
            if attempt_id and lease_token and store is not None:
                row = store.get_job(job_id)
                if row and row.get("status") in {"queued", "running", "cancelling"}:
                    if store.update_job_if_owned(
                        job_id, attempt_id, lease_token,
                        status="interrupted", step="interrupted", error=reason,
                    ):
                        store.append_log(job_id, "ERROR", reason)
            else:
                row = db_get_job(job_id)
                if row and row.get("status") in {"queued", "running", "cancelling"}:
                    db_update_job(job_id, status="interrupted", step="interrupted", error=reason)
                    db_append_log(job_id, "ERROR", reason)
        except Exception:
            logger.exception("Could not reconcile worker exit for %s", job_id)
    finally:
        with _active_processes_lock:
            _active_processes.pop(job_id, None)
            row = db_get_job(job_id)
            owns_current_attempt = bool(
                row
                and (
                    not attempt_id
                    or not lease_token
                    or (
                        row.get("attempt_id") == attempt_id
                        and row.get("worker_token") == lease_token
                    )
                )
            )
            if owns_current_attempt:
                if row and row.get("status") in {"error", "interrupted"}:
                    _retain_runtime_secrets_for_retry(job_id)
                else:
                    _runtime_secret_expiry.pop(job_id, None)
                    _runtime_secrets.pop(job_id, None)
        if worker_id:
            try:
                from dashboard_store import DashboardStore
                DashboardStore(DB_PATH).set_worker_status(worker_id, "idle")
            except Exception:
                logger.debug("Unable to update worker registry after exit", exc_info=True)
        _invalidate_package_cache()
        try:
            _pump_queued_jobs()
        except Exception:
            logger.exception("Queue pump failed after worker exit")


def _running_count() -> int:
    with _active_processes_lock:
        dead = [job_id for job_id, process in _active_processes.items() if not process.is_alive()]
        for job_id in dead:
            _active_processes.pop(job_id, None)
        return sum(1 for process in _active_processes.values() if process.is_alive())


def _start_job(job_id: str, params: dict, secrets: dict) -> bool:
    from dashboard_store import DashboardStore

    with _active_processes_lock:
        if _running_count() >= max(1, int(get_settings()["max_concurrent_jobs"])):
            return False

        attempt_id = f"attempt_{uuid.uuid4().hex[:16]}"
        lease_token = uuid.uuid4().hex + uuid.uuid4().hex
        worker_id = f"worker-{uuid.uuid4().hex[:12]}"
        workspace_root = OUTPUT_FOLDER / ".workspaces" / secure_filename(str(job_id))
        workspace_dir = workspace_root / attempt_id
        publish_dir = OUTPUT_FOLDER / f"{_safe_topic_slug(params['topic'])}_{uuid.uuid4().hex[:10]}"
        workspace_dir.mkdir(parents=True, exist_ok=False)

        store = DashboardStore(DB_PATH)
        try:
            if not store.claim_job(
                job_id,
                attempt_id=attempt_id,
                worker_id=worker_id,
                lease_token=lease_token,
                workspace_dir=str(workspace_dir),
            ):
                shutil.rmtree(workspace_dir, ignore_errors=True)
                return False

            from dashboard_worker import run_job
            ctx = multiprocessing.get_context("spawn")
            process = ctx.Process(
                target=run_job,
                args=(
                    job_id, params, secrets, str(OUTPUT_FOLDER), str(DB_PATH),
                    attempt_id, lease_token, worker_id, str(workspace_dir), str(publish_dir),
                ),
                daemon=False,
            )
            try:
                process.start()
            except Exception:
                store.update_job_if_owned(
                    job_id, attempt_id, lease_token,
                    status="error", step="failed", error="Worker failed to start",
                )
                shutil.rmtree(workspace_root, ignore_errors=True)
                raise
            store.register_worker(
                worker_id,
                process.pid,
                capabilities={"workflow": str(params.get("workflow", "default"))},
            )
            _active_processes[job_id] = process
            threading.Thread(
                target=_watch_job_process,
                args=(job_id, process, attempt_id, lease_token, worker_id),
                name=f"aivf-reaper-{job_id}",
                daemon=True,
            ).start()
            return True
        except Exception:
            shutil.rmtree(workspace_root, ignore_errors=True)
            raise


def _pump_queued_jobs() -> int:
    """Start queued jobs whenever durable admission and local worker capacity permit."""
    from dashboard_store import DashboardStore

    started = 0
    with _queue_pump_lock:
        store = DashboardStore(DB_PATH)
        try:
            GLOBAL_METRICS.set_gauge("queue_depth", store.queued_count())
            GLOBAL_METRICS.set_gauge("disk_free_bytes", float(shutil.disk_usage(OUTPUT_FOLDER).free))
        except Exception:
            logger.debug("Unable to update queue/resource gauges", exc_info=True)
        rows = store.list_queued_jobs(limit=_MAX_QUEUED_JOBS)
        for row in rows:
            if str(row.get("status")) != "queued":
                continue
            job_id = str(row.get("id") or "")
            if not job_id:
                continue
            try:
                params = json.loads(row.get("params") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                store.update_job(job_id, status="error", step="failed", error="Queued job parameters are invalid", error_code="input_invalid")
                continue
            if not isinstance(params, dict):
                continue
            secrets_payload = dict(_runtime_secrets.get(job_id) or _runtime_default_secrets)
            required_secrets = {
                str(name).strip()
                for name in (params.get("_retry_secret_keys") or [])
                if str(name).strip()
            }
            missing_secrets = sorted(
                name for name in required_secrets if not secrets_payload.get(name)
            )
            if missing_secrets:
                store.update_job(
                    job_id,
                    status="interrupted",
                    step="credentials_required",
                    error="Required credentials are unavailable after restart: " + ", ".join(missing_secrets),
                    error_code="credentials_required",
                )
                continue
            try:
                if _start_job(job_id, params, secrets_payload):
                    started += 1
            except Exception:
                logger.exception("Failed to start queued job %s", job_id)
            if _running_count() >= max(1, int(get_settings().get("max_concurrent_jobs", 2))):
                break
    return started


def _queue_pump_loop() -> None:
    while True:
        try:
            _pump_queued_jobs()
        except Exception:
            logger.exception("Queue pump failed")
        time.sleep(_QUEUE_PUMP_INTERVAL_SECONDS)


def _start_queue_pump() -> None:
    if os.environ.get("AIVF_WORKER_PROCESS") == "1":
        return
    enabled = os.environ.get("AIVF_QUEUE_PUMP_ENABLED", "1").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return
    threading.Thread(target=_queue_pump_loop, name="aivf-queue-pump", daemon=True).start()

def _resolve_package(name: str) -> Optional[Path]:
    raw_name = str(name)
    safe_name = secure_filename(raw_name)
    if not safe_name or safe_name != raw_name or safe_name in {".", ".."}:
        return None
    root = OUTPUT_FOLDER.resolve()
    candidate = (root / safe_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_dir():
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
        role_gate = app.extensions.get("aivf_require_role")
        if callable(role_gate):
            role_gate("admin")
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Settings payload must be a JSON object"}), 400
        try:
            for key, value in data.items():
                set_setting(key, value)
            from dashboard_store import DashboardStore
            store = DashboardStore(DB_PATH)
            principal_for_audit = str(session.get("aivf_principal") or request.remote_addr or "unknown")
            store.record_audit_event(
                principal_for_audit,
                "SETTINGS_CHANGED",
                "settings",
                result="success",
                metadata=json.dumps(sorted(data.keys())),
                ip_address=request.remote_addr,
                user_agent=request.headers.get("User-Agent"),
            )
        except ValueError as exc:
            return jsonify({"error": "Invalid settings payload"}), 400
    return jsonify(get_settings())


def _strip_idempotency_volatile(value: Any) -> Any:
    """Remove server-generated volatile declaration timestamps recursively."""
    if isinstance(value, dict):
        return {
            key: _strip_idempotency_volatile(item)
            for key, item in value.items()
            if key != "declared_at"
        }
    if isinstance(value, list):
        return [_strip_idempotency_volatile(item) for item in value]
    return value


def _persisted_input_media_hash(existing_job: Mapping[str, Any], existing_params: Mapping[str, Any]) -> Optional[str]:
    """Recover an old job's source-media hash from its persisted provenance."""
    package_candidates = [
        existing_job.get("pkg_dir"),
        existing_params.get("pkg_dir"),
    ]
    output_root = OUTPUT_FOLDER.resolve()
    for raw_package in package_candidates:
        if not raw_package:
            continue
        try:
            package = Path(str(raw_package)).resolve()
        except (OSError, RuntimeError):
            continue
        if package == output_root or output_root not in package.parents:
            continue
        provenance_path = package / "provenance.json"
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        assets = provenance.get("assets") if isinstance(provenance, dict) else None
        if not isinstance(assets, list):
            continue
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            asset_id = str(asset.get("asset_id") or "").strip().lower()
            media_hash = str(asset.get("sha256") or "").strip().lower()
            if asset_id == "source_video" and re.fullmatch(r"[0-9a-f]{64}", media_hash):
                return media_hash
    return None


def _legacy_idempotency_projection(value: Any, template: Any, *, top_level: bool = False) -> Any:
    """Project a current request onto fields present in an older stored request."""
    if isinstance(value, dict) and isinstance(template, dict):
        ignored = {"_principal", "_retry_secret_keys", "raw_video", "pkg_dir"} if top_level else set()
        return {
            key: _legacy_idempotency_projection(value[key], template_value)
            for key, template_value in template.items()
            if key in value and key not in ignored
        }
    if isinstance(value, list) and isinstance(template, list):
        # Lists were already part of the legacy contract; preserve their complete value.
        return [_strip_idempotency_volatile(item) for item in value]
    return _strip_idempotency_volatile(value)


def _legacy_request_fingerprint(params: Mapping[str, Any], existing_params: Mapping[str, Any]) -> str:
    """Fingerprint only the request fields understood by the legacy stored hash."""
    projected = _legacy_idempotency_projection(params, existing_params, top_level=True)
    return request_idempotency_hash(
        _strip_idempotency_volatile(projected),
        namespace="dashboard-request",
    )


def _request_fingerprint(params: dict, upload_path: Optional[Path] = None) -> str:
    """Fingerprint request intent and, when present, the uploaded media bytes."""
    payload = {
        key: value
        for key, value in params.items()
        if key not in {"_principal", "_retry_secret_keys", "raw_video", "pkg_dir"}
    }
    payload = _strip_idempotency_volatile(payload)
    if upload_path is not None:
        payload["_input_media_sha256"] = sha256_file(upload_path)
    return request_idempotency_hash(payload, namespace="dashboard-request")


@app.route("/api/jobs", methods=["POST"])
def create_job():
    role_gate = app.extensions.get("aivf_require_role")
    if callable(role_gate):
        role_gate("editor")
    client_ip = request.remote_addr or "unknown"
    if not check_rate_limit(client_ip):
        return jsonify({"error": "Rate limit exceeded. Try again later."}), 429
    data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    settings_data = get_settings()
    try:
        params = build_job_params(
            data,
            settings_data,
            allow_skip_qc=os.environ.get("AIVF_ALLOW_SKIP_QC", "0") == "1",
        )
    except (TypeError, ValueError) as exc:
        logger.info("Job request validation failed: %s", type(exc).__name__)
        return jsonify({"error": "Invalid job request"}), 400

    topic = params["topic"]
    workflow = params["workflow"]
    idem_key = request.headers.get("Idempotency-Key", "").strip()
    if idem_key and not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", idem_key):
        return jsonify({"error": "Idempotency-Key contains unsupported characters"}), 400
    from resource_governor import principal_for_request, MAX_QUEUED_PER_PRINCIPAL
    principal = principal_for_request(request)
    params["_principal"] = principal

    source_meta = params.get("source_metadata")
    if isinstance(source_meta, dict):
        rights_basis = str(source_meta.get("rights_basis") or "").strip().lower()
        if rights_basis:
            from ai_video_factory.rights_policy import rights_gate
            source_meta.update({
                "rights_status": rights_basis,
                "evidence_url": str(source_meta.get("evidence_url") or "").strip(),
                "declared_by": principal,
                "declared_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            })
            rights_check = rights_gate([{
                "asset_id": "source_video",
                "source": "user_upload",
                "rights_basis": rights_basis,
                "evidence_url": source_meta["evidence_url"],
                "license_url": str(source_meta.get("license_url") or "").strip(),
                "declared_by": principal,
                "declared_at": source_meta["declared_at"],
            }], strict=True)
            if rights_check.get("publish_blocked"):
                return jsonify({
                    "error": "Selected rights basis needs supporting evidence",
                    "rights_errors": [
                        item.get("errors", [])
                        for item in (rights_check.get("checked") or [])
                        if isinstance(item, dict) and item.get("errors")
                    ],
                }), 400

    target_seconds = params["target_seconds"]
    secrets = get_runtime_default_secrets()
    secrets.update({key: str(data.get(key, "")).strip() for key in SECRET_PARAM_KEYS if data.get(key)})
    params["_retry_secret_keys"] = [key for key in SECRET_PARAM_KEYS if data.get(key)]

    if workflow == "v3":
        capabilities = _runtime_capabilities()
        requested = (
            ("enable_ocr", "ocr", "OCR"),
            ("enable_object_detection", "object_detection", "Object detection"),
            ("enable_diarization", "diarization", "Speaker diarization"),
        )
        for param_key, capability_key, display_name in requested:
            if params.get(param_key) and not capabilities[capability_key]:
                return jsonify({"error": f"{display_name} is not available in this production environment"}), 400
        if params.get("enable_diarization") and not secrets.get("diarization_token"):
            return jsonify({"error": "Speaker diarization is enabled but no diarization token is configured"}), 400
    if workflow == "v3" and not (request.files.get("raw_video") and request.files.get("raw_video").filename):
        return jsonify({"error": "V3 production requires a raw video upload"}), 400

    upload = request.files.get("raw_video")
    upload_path: Optional[Path] = None
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
            upload_result = _save_and_validate_upload(upload, suffix)
            if isinstance(upload_result, tuple):
                upload_path, media_summary = upload_result
            else:
                # Backward-compatible seam for legacy tests/callers that return only Path.
                upload_path = upload_result
                media_summary = {}
            if isinstance(media_summary, Mapping) and media_summary:
                resource_budget = estimate_resource_budget(
                    media_summary,
                    input_size_bytes=upload_path.stat().st_size,
                    limits=DEFAULT_MEDIA_LIMITS,
                )
                params["_media_summary"] = media_summary
                params["_resource_budget"] = {
                    "resource_units": resource_budget.resource_units,
                    "resource_class": resource_budget.resource_class,
                    "reserved_disk_bytes": resource_budget.reserved_disk_bytes,
                    "reserved_memory_bytes": resource_budget.reserved_memory_bytes,
                    "estimated_job_seconds": resource_budget.estimated_job_seconds,
                }
            remaining = shutil.disk_usage(UPLOAD_FOLDER).free
            if remaining < _MIN_FREE_DISK_BYTES:
                upload_path.unlink(missing_ok=True)
                return jsonify({"error": "Server disk space is too low after upload"}), 503
        except (OSError, ValueError):
            return jsonify({"error": "Upload is too large or is not a valid supported video stream"}), 400
        except GuardrailError:
            return jsonify({"error": "Media validation failed"}), 400
        params["raw_video"] = str(upload_path)

    request_hash = ""
    if idem_key:
        try:
            request_hash = _request_fingerprint(params, upload_path)
        except OSError:
            upload_path.unlink(missing_ok=True)
            return jsonify({"error": "Uploaded video could not be fingerprinted safely"}), 400

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    try:
        from dashboard_store import DashboardStore, IdempotencyConflict, JobAdmissionError
        from resource_governor import ResourceLimitExceeded, check_job_creation_limits
        configured_store = globals().get("dashboard_store")
        store = configured_store
        if store is None or Path(store.db_path).resolve() != DB_PATH.resolve():
            # Test reloads and app factory boundaries can leave an old store
            # object on the module. Never write a new request into another
            # deployment/test database.
            store = DashboardStore(DB_PATH)
        if idem_key:
            existing_idempotency = store.lookup_idempotency(
                principal=principal,
                idempotency_key=idem_key,
            )
            if existing_idempotency is not None:
                existing_job_id, existing_request_hash = existing_idempotency
                if existing_request_hash != request_hash:
                    legacy_replay_safe = False
                    existing = store.get_job(existing_job_id) or {}
                    raw_existing_params = existing.get("params")
                    try:
                        existing_params = json.loads(raw_existing_params or "{}") if isinstance(raw_existing_params, str) else (raw_existing_params or {})
                    except (TypeError, ValueError, json.JSONDecodeError):
                        existing_params = {}
                    try:
                        legacy_current_hash = _legacy_request_fingerprint(params, existing_params)
                        legacy_existing_hash = _legacy_request_fingerprint(existing_params, existing_params)
                    except Exception:
                        legacy_current_hash = ""
                        legacy_existing_hash = ""
                    if legacy_current_hash == existing_request_hash and legacy_existing_hash == existing_request_hash:
                        current_media_hash = sha256_file(upload_path) if upload_path is not None else None
                        original_path = Path(str(existing_params.get("raw_video") or "")).resolve() if existing_params.get("raw_video") else None
                        upload_root = UPLOAD_FOLDER.resolve()
                        original_media_hash = None
                        if original_path is not None and upload_root in original_path.parents and original_path.is_file():
                            try:
                                original_media_hash = sha256_file(original_path)
                            except OSError:
                                original_media_hash = None
                        if original_media_hash is None:
                            original_media_hash = _persisted_input_media_hash(existing, existing_params)
                        legacy_replay_safe = (
                            (current_media_hash is None and original_media_hash is None)
                            or (
                                current_media_hash is not None
                                and original_media_hash is not None
                                and current_media_hash == original_media_hash
                            )
                        )
                    if not legacy_replay_safe:
                        if upload_path is not None:
                            upload_path.unlink(missing_ok=True)
                        return jsonify({"error": "Idempotency key was already used for a different request"}), 409
                    # Upgrade only after proving the request intent and media bytes
                    # still identify the exact original job.
                    if not store.migrate_idempotency_hash(
                        principal=principal,
                        idempotency_key=idem_key,
                        expected_old_hash=existing_request_hash,
                        new_hash=request_hash,
                    ):
                        refreshed = store.lookup_idempotency(
                            principal=principal,
                            idempotency_key=idem_key,
                        )
                        if refreshed is None or refreshed[1] != request_hash:
                            if upload_path is not None:
                                upload_path.unlink(missing_ok=True)
                            return jsonify({"error": "Idempotency key could not be safely migrated"}), 409
                if upload_path is not None:
                    upload_path.unlink(missing_ok=True)
                existing = store.get_job(existing_job_id) or {}
                return jsonify({
                    "job_id": existing_job_id,
                    "status": existing.get("status", "queued"),
                    "idempotent_replay": True,
                }), 200
        try:
            check_job_creation_limits(
                store.connect,
                principal,
                (UPLOAD_FOLDER, OUTPUT_FOLDER),
            )
        except ResourceLimitExceeded as exc:
            # A concurrent identical request may have committed the idempotency
            # key between our initial lookup and this admission check. Re-check
            # before returning 429 so a replay never fails because the first
            # request consumed the newly-created queue slot.
            if idem_key:
                concurrent = store.lookup_idempotency(
                    principal=principal,
                    idempotency_key=idem_key,
                )
                if concurrent is not None:
                    existing_job_id, existing_request_hash = concurrent
                    if existing_request_hash == request_hash:
                        if upload_path is not None:
                            upload_path.unlink(missing_ok=True)
                        existing = store.get_job(existing_job_id) or {}
                        return jsonify({
                            "job_id": existing_job_id,
                            "status": existing.get("status", "queued"),
                            "idempotent_replay": True,
                        }), 200
                    if upload_path is not None:
                        upload_path.unlink(missing_ok=True)
                    return jsonify({"error": "Idempotency key was already used for a different request"}), 409
            if upload_path is not None:
                upload_path.unlink(missing_ok=True)
            logger.warning("Job admission resource limit reached: %s", exc)
            return jsonify({"error": "Job admission resource limit reached"}), 429
        if idem_key:
            actual_job_id, created = store.insert_job_idempotent(
                job_id,
                topic,
                params,
                principal=principal,
                idempotency_key=idem_key,
                request_hash=request_hash,
                max_queued_jobs=_MAX_QUEUED_JOBS,
                principal_limit=MAX_QUEUED_PER_PRINCIPAL,
                resource_units=params.get("_resource_budget", {}).get("resource_units"),
                resource_class=params.get("_resource_budget", {}).get("resource_class"),
                reserved_disk_bytes=params.get("_resource_budget", {}).get("reserved_disk_bytes"),
                reserved_memory_bytes=params.get("_resource_budget", {}).get("reserved_memory_bytes"),
                available_disk_bytes=max(
                    0,
                    shutil.disk_usage(OUTPUT_FOLDER).free - DEFAULT_MEDIA_LIMITS.min_free_disk_bytes,
                ),
                resource_capacity_units=DEFAULT_MEDIA_LIMITS.resource_capacity_units,
                max_reserved_memory_bytes=DEFAULT_MEDIA_LIMITS.max_reserved_memory_bytes,
            )
            if not created:
                if upload_path is not None:
                    upload_path.unlink(missing_ok=True)
                existing = store.get_job(actual_job_id) or {}
                return jsonify({"job_id": actual_job_id, "status": existing.get("status", "queued"), "idempotent_replay": True}), 200
        else:
            store.insert_job(
                job_id,
                topic,
                params,
                max_queued_jobs=_MAX_QUEUED_JOBS,
                principal=principal,
                principal_limit=MAX_QUEUED_PER_PRINCIPAL,
                resource_units=params.get("_resource_budget", {}).get("resource_units"),
                resource_class=params.get("_resource_budget", {}).get("resource_class"),
                reserved_disk_bytes=params.get("_resource_budget", {}).get("reserved_disk_bytes"),
                reserved_memory_bytes=params.get("_resource_budget", {}).get("reserved_memory_bytes"),
                available_disk_bytes=max(
                    0,
                    shutil.disk_usage(OUTPUT_FOLDER).free - DEFAULT_MEDIA_LIMITS.min_free_disk_bytes,
                ),
                resource_capacity_units=DEFAULT_MEDIA_LIMITS.resource_capacity_units,
                max_reserved_memory_bytes=DEFAULT_MEDIA_LIMITS.max_reserved_memory_bytes,
            )
    except IdempotencyConflict as exc:
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
        logger.info("Idempotency conflict for job creation request")
        return jsonify({"error": "Idempotency key was already used for a different request"}), 409
    except JobAdmissionError as exc:
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
        GLOBAL_METRICS.increment("job_admission_rejections_total")
        logger.info("Job admission limit rejected request: %s", type(exc).__name__)
        return jsonify({"error": "Job admission limit reached"}), 429
    except Exception:
        if upload_path is not None:
            upload_path.unlink(missing_ok=True)
        raise

    try:
        from dashboard_store import DashboardStore
        DashboardStore(DB_PATH).record_audit_event(
            principal,
            "JOB_CREATED",
            "job",
            resource_id=job_id,
            metadata=json.dumps({"workflow": workflow, "idempotent": bool(idem_key)}),
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
    except Exception:
        logger.debug("Audit event could not be recorded for job creation", exc_info=True)

    _runtime_secrets[job_id] = secrets
    cache = globals().get("dashboard_cache")
    if cache is not None:
        try:
            cache.delete("jobs:list")
        except Exception:
            logger.debug("Unable to invalidate dashboard job-list cache", exc_info=True)
    _pump_queued_jobs()
    return jsonify({"job_id": job_id, "status": "queued"}), 202

@app.route("/api/jobs", methods=["GET"])
def list_jobs():
    return jsonify([_redact_job(job) for job in db_list_jobs()])


@app.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    lookup = globals().get("authorized_db_get_job", db_get_job)
    job = lookup(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(_redact_job(job, include_logs=True))


@app.route("/api/jobs/<job_id>/status")
def get_job_status(job_id):
    lookup = globals().get("authorized_db_get_job", db_get_job)
    job = lookup(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({"id": job_id, "status": job["status"], "step": job["step"], "error": job["error"]})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    role_gate = app.extensions.get("aivf_require_role")
    if callable(role_gate):
        role_gate("editor")
    from dashboard_compat import cancel_process
    result = cancel_process(job_id)
    try:
        from resource_governor import principal_for_request
        from dashboard_store import DashboardStore
        DashboardStore(DB_PATH).record_audit_event(
            principal_for_request(request),
            "JOB_CANCEL_REQUESTED",
            "job",
            resource_id=job_id,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
    except Exception:
        logger.debug("Audit event could not be recorded for job cancellation", exc_info=True)
    return result


@app.route("/api/jobs/<job_id>/logs")
@app.route("/api/jobs/<job_id>/logs/stream")
def job_logs_stream(job_id):
    lookup = globals().get("authorized_db_get_job", db_get_job)
    authorized_job = lookup(job_id)
    if not authorized_job:
        return jsonify({"error": "Job not found"}), 404

    raw_last_id = request.headers.get("Last-Event-ID", "0").strip()
    try:
        initial_last_id = max(0, int(raw_last_id))
    except ValueError:
        initial_last_id = 0

    def stream():
        last_id = initial_last_id
        job = dict(authorized_job)
        while True:
            current = db_get_job(job_id)
            if not current:
                yield "data: " + json.dumps({"level": "ERROR", "msg": "Job not found"}) + "\n\n"
                return
            job["status"] = current.get("status")
            for row in db_logs_since(job_id, last_id):
                last_id = row["id"]
                yield (
                    "id: " + str(last_id) + "\n"
                    "data: " + json.dumps({
                        "time": row["created_at"],
                        "level": row["level"],
                        "msg": row["message"],
                    }) + "\n\n"
                )
            if job["status"] in TERMINAL_STATUSES:
                return
            yield ": heartbeat\n\n"
            time.sleep(0.5)
    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _package_access_allowed(pkg_dir: Optional[Path]) -> bool:
    """Allow package access only to admins or the principal that owns the job."""
    if pkg_dir is None or not pkg_dir.is_dir():
        return False
    from flask import session
    if not app.config.get("_AIVF_AUTH_CONFIGURED"):
        return True
    if not session.get("aivf_authenticated"):
        return False
    if str(session.get("aivf_role") or "viewer").strip().lower() == "admin":
        return True
    try:
        from resource_governor import principal_for_request
        principal = principal_for_request(request)
        with get_db() as conn:
            row = conn.execute(
                "SELECT principal FROM jobs WHERE pkg_dir=? LIMIT 1",
                (str(pkg_dir),),
            ).fetchone()
        return bool(row and str(row["principal"] or "") == principal)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return False


def _request_is_admin() -> bool:
    from flask import session
    return (
        not app.config.get("_AIVF_AUTH_CONFIGURED")
        or (
            bool(session.get("aivf_authenticated"))
            and str(session.get("aivf_role") or "viewer").strip().lower() == "admin"
        )
    )


@app.route("/api/packages")
def list_packages():
    global _package_cache
    now = time.monotonic()
    use_cache = _request_is_admin()
    if use_cache:
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
            if not _package_access_allowed(pkg_path):
                continue
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

    if use_cache:
        with _package_cache_lock:
            _package_cache = (time.monotonic(), packages)
    return jsonify(packages)


def _resolve_package_file(package: Optional[Path], filename: str) -> Optional[Path]:
    """Resolve an existing regular file discovered beneath a package root."""
    if package is None or not isinstance(filename, str):
        return None
    requested = filename.replace("\\", "/")
    if not requested or requested.startswith("/") or requested.startswith("../") or "/../" in requested:
        return None
    root = package.resolve()
    try:
        candidates = package.rglob("*")
    except OSError:
        return None
    for candidate in candidates:
        try:
            if not candidate.is_file() or candidate.is_symlink():
                continue
            relative = candidate.relative_to(package).as_posix()
            if relative != requested:
                continue
            resolved = candidate.resolve()
            resolved.relative_to(root)
            return resolved
        except (OSError, ValueError):
            continue
    return None


@app.route("/api/packages/<name>/file/<path:filename>")
def package_file(name, filename):
    pkg_dir = _resolve_package(name)
    if not _package_access_allowed(pkg_dir):
        abort(404)
    if pkg_dir is None:
        abort(404)
    resolved = _resolve_package_file(pkg_dir, filename)
    if resolved is None:
        abort(404)
    return send_file(resolved)


@app.route("/api/health")
def health():
    """Minimal unauthenticated liveness/readiness response."""
    return jsonify({"status": "ok"})


@app.route("/api/health/details")
def health_details():
    """Authenticated operational health; detailed capacity data is not public."""
    usage = shutil.disk_usage(UPLOAD_FOLDER)
    return jsonify({
        "status": "ok",
        "disk_free_mb": round(usage.free / (1024 * 1024), 1),
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "ffprobe_available": shutil.which("ffprobe") is not None,
        "active_jobs": _running_count(),
        "max_content_length_mb": app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
        "max_concurrent_jobs": int(get_settings().get("max_concurrent_jobs", 2)),
        "capabilities": _runtime_capabilities(),
    })


init_db()

if __name__ == "__main__":
    _start_queue_pump()
    from dashboard_auth import configure_dashboard_auth
    from dashboard_compat import register_dashboard_compat
    configure_dashboard_auth(app)
    register_dashboard_compat(app)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
