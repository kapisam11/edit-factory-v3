"""Durable autonomous YouTube operating loop for Edit Factory v3.

The module adds a single-host, SQLite-backed state machine around the existing
production pipeline.  It is intentionally conservative: no automatic publish
occurs unless the package passes media, factuality, rights, originality/style,
budget and disclosure gates.  Public metadata contains no factory/vendor
branding; platform-required disclosure remains separate and cannot be bypassed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .complete_factory import PLATFORM_LAYOUTS, run_complete_factory
from .feedback_store import FeedbackStore
from .human_style_guard import assess_package, sanitize_public_metadata
from .performance_learning import ChannelPerformanceModel, PerformanceObservation
from .youtube_publisher import fetch_video_analytics, fetch_video_statistics, upload_video

logger = logging.getLogger(__name__)


STATES = {
    "IDEA",
    "PRODUCING",
    "READY",
    "POLICY_REVIEW",
    "SCHEDULED",
    "UPLOADING",
    "PUBLISHED",
    "ANALYZING",
    "ANALYZED",
    "FAILED",
}


@dataclass(frozen=True)
class AutonomousConfig:
    state_dir: Path
    output_dir: Path
    queue_db: Path
    max_videos_per_day: int = 2
    max_queue_size: int = 30
    max_consecutive_failures: int = 3
    max_daily_cost_usd: float = 5.0
    estimated_cost_per_video_usd: float = 0.0
    max_retries: int = 4
    retry_base_seconds: int = 60
    retry_max_seconds: int = 3600
    analytics_after_hours: float = 24.0
    scheduler_interval_seconds: int = 60
    publish_times_utc: tuple[str, ...] = ("12:00", "18:00")
    autonomous_publish: bool = False
    require_human_approval: bool = True
    failure_webhook_url: str = ""
    retention_days: int = 30

    @classmethod
    def from_environment(cls) -> "AutonomousConfig":
        state_dir = Path(os.environ.get("AIVF_STATE_DIR", "./state")).expanduser().resolve()
        output_dir = Path(os.environ.get("AIVF_OUTPUT_DIR", "./output")).expanduser().resolve()
        publish_raw = os.environ.get("AIVF_PUBLISH_TIMES_UTC", "12:00,18:00")
        times = tuple(item.strip() for item in publish_raw.split(",") if item.strip()) or ("12:00", "18:00")
        autonomous = os.environ.get("AIVF_AUTONOMOUS_PUBLISH", "0").strip() == "1"
        require_human = os.environ.get(
            "AIVF_AUTONOMOUS_REQUIRE_HUMAN_APPROVAL",
            "0" if autonomous else "1",
        ).strip() == "1"
        return cls(
            state_dir=state_dir,
            output_dir=output_dir,
            queue_db=state_dir / "autonomous-youtube.db",
            max_videos_per_day=max(0, int(os.environ.get("AIVF_MAX_VIDEOS_PER_DAY", "2"))),
            max_queue_size=max(1, int(os.environ.get("AIVF_MAX_CONTENT_QUEUE", "30"))),
            max_consecutive_failures=max(1, int(os.environ.get("AIVF_MAX_CONSECUTIVE_FAILURES", "3"))),
            max_daily_cost_usd=max(0.0, float(os.environ.get("AIVF_MAX_DAILY_COST_USD", "5"))),
            estimated_cost_per_video_usd=max(0.0, float(os.environ.get("AIVF_ESTIMATED_COST_PER_VIDEO_USD", "0"))),
            max_retries=max(0, int(os.environ.get("AIVF_MAX_PUBLISH_RETRIES", "4"))),
            retry_base_seconds=max(1, int(os.environ.get("AIVF_RETRY_BASE_SECONDS", "60"))),
            retry_max_seconds=max(1, int(os.environ.get("AIVF_RETRY_MAX_SECONDS", "3600"))),
            analytics_after_hours=max(0.25, float(os.environ.get("AIVF_ANALYTICS_AFTER_HOURS", "24"))),
            scheduler_interval_seconds=max(10, int(os.environ.get("AIVF_AUTOMATION_INTERVAL_SECONDS", "60"))),
            publish_times_utc=times,
            autonomous_publish=autonomous,
            require_human_approval=require_human,
            failure_webhook_url=os.environ.get("AIVF_FAILURE_WEBHOOK_URL", "").strip(),
            retention_days=max(1, int(os.environ.get("AIVF_AUTOMATION_RETENTION_DAYS", "30"))),
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_json(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return default


class AutonomousStore:
    def __init__(self, config: AutonomousConfig) -> None:
        self.config = config
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.config.queue_db, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS autonomous_jobs (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    scheduled_at TEXT,
                    package_dir TEXT,
                    title TEXT,
                    video_id TEXT,
                    fingerprint TEXT,
                    video_sha256 TEXT,
                    script TEXT NOT NULL DEFAULT '',
                    estimated_cost_usd REAL NOT NULL DEFAULT 0,
                    actual_cost_usd REAL NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    approval TEXT NOT NULL DEFAULT 'pending',
                    publish_requested INTEGER NOT NULL DEFAULT 0,
                    ai_generated INTEGER NOT NULL DEFAULT 0,
                    realistic_alteration INTEGER NOT NULL DEFAULT 0,
                    rights_json TEXT NOT NULL DEFAULT '{}',
                    quality_json TEXT NOT NULL DEFAULT '{}',
                    analytics_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_auto_state_schedule
                    ON autonomous_jobs(state, scheduled_at, next_attempt_at);
                CREATE INDEX IF NOT EXISTS idx_auto_created
                    ON autonomous_jobs(created_at);
                CREATE TABLE IF NOT EXISTS autonomous_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT,
                    event TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_auto_events_job
                    ON autonomous_events(job_id, id);
                CREATE TABLE IF NOT EXISTS autonomous_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS autonomous_analytics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    metrics TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_auto_analytics_video_time
                    ON autonomous_analytics(video_id, observed_at);
                """
            )
            for key, value in {
                "paused": "0",
                "emergency_stop": "0",
                "consecutive_failures": "0",
            }.items():
                conn.execute(
                    "INSERT INTO autonomous_settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO NOTHING",
                    (key, value),
                )

    def setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM autonomous_settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO autonomous_settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def _event(self, conn: sqlite3.Connection, job_id: Optional[str], event: str, details: Any = "") -> None:
        conn.execute(
            "INSERT INTO autonomous_events(job_id,event,details,created_at) VALUES(?,?,?,?)",
            (job_id, event, json.dumps(details, sort_keys=True, default=str) if not isinstance(details, str) else details, _utc_now()),
        )

    def enqueue(
        self,
        topic: str,
        *,
        platform: str = "youtube_shorts",
        scheduled_at: Optional[str] = None,
        priority: int = 0,
        publish_requested: bool = True,
        options: Optional[Mapping[str, Any]] = None,
    ) -> str:
        clean_topic = str(topic or "").strip()
        if not clean_topic:
            raise ValueError("topic is required")
        if platform not in PLATFORM_LAYOUTS:
            raise ValueError(f"unsupported platform: {platform}")
        with self._connect() as conn:
            active = conn.execute(
                "SELECT COUNT(*) AS n FROM autonomous_jobs "
                "WHERE state IN ('IDEA','PRODUCING','READY','POLICY_REVIEW','SCHEDULED','UPLOADING','ANALYZING')"
            ).fetchone()["n"]
            if int(active) >= self.config.max_queue_size:
                raise RuntimeError("autonomous queue is full")
        seed = f"{clean_topic}|{platform}|{scheduled_at or ''}|{json.dumps(dict(options or {}), sort_keys=True)}"
        job_id = "yt-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO autonomous_jobs(
                    id,topic,state,created_at,updated_at,priority,scheduled_at,
                    estimated_cost_usd,publish_requested
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    priority=excluded.priority,
                    scheduled_at=excluded.scheduled_at,
                    publish_requested=excluded.publish_requested,
                    updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    clean_topic,
                    "IDEA",
                    now,
                    now,
                    int(priority),
                    scheduled_at,
                    self.config.estimated_cost_per_video_usd,
                    1 if publish_requested else 0,
                ),
            )
            self._event(conn, job_id, "queued", {"topic": clean_topic, "platform": platform})
        return job_id

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM autonomous_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM autonomous_jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _claim(self, states: Sequence[str]) -> Optional[dict[str, Any]]:
        placeholders = ",".join("?" for _ in states)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"SELECT * FROM autonomous_jobs "
                f"WHERE state IN ({placeholders}) AND "
                "(next_attempt_at IS NULL OR next_attempt_at<=?) "
                "ORDER BY priority DESC, COALESCE(scheduled_at,created_at), created_at LIMIT 1",
                (*states, now),
            ).fetchone()
            if not row:
                conn.commit()
                return None
            new_state = "PRODUCING" if row["state"] in {"IDEA", "FAILED"} else row["state"]
            conn.execute(
                "UPDATE autonomous_jobs SET state=?,updated_at=?,error='' WHERE id=?",
                (new_state, now, row["id"]),
            )
            self._event(conn, row["id"], "claimed", {"from": row["state"], "to": new_state})
            conn.commit()
            updated = conn.execute("SELECT * FROM autonomous_jobs WHERE id=?", (row["id"],)).fetchone()
        return dict(updated) if updated else None

    def claim_production(self) -> Optional[dict[str, Any]]:
        return self._claim(("IDEA", "FAILED"))

    def claim_analysis(self) -> Optional[dict[str, Any]]:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM autonomous_jobs WHERE state='PUBLISHED' "
                "AND published_at IS NOT NULL "
                "AND published_at<=? ORDER BY published_at LIMIT 1",
                (now,),
            ).fetchone()
            if not row:
                conn.commit()
                return None
            conn.execute("UPDATE autonomous_jobs SET state='ANALYZING',updated_at=? WHERE id=?", (now, row["id"]))
            self._event(conn, row["id"], "analytics_claimed")
            conn.commit()
        return dict(row)

    def update(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "state",
            "updated_at",
            "scheduled_at",
            "package_dir",
            "title",
            "video_id",
            "fingerprint",
            "video_sha256",
            "script",
            "estimated_cost_usd",
            "actual_cost_usd",
            "retry_count",
            "next_attempt_at",
            "approval",
            "publish_requested",
            "ai_generated",
            "realistic_alteration",
            "rights_json",
            "quality_json",
            "analytics_json",
            "error",
            "published_at",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"invalid autonomous job fields: {sorted(invalid)}")
        values = dict(fields)
        values.setdefault("updated_at", _utc_now())
        assignments = ",".join(f"{key}=?" for key in values)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE autonomous_jobs SET {assignments} WHERE id=?",
                (*values.values(), job_id),
            )

    def approve(self, job_id: str, actor: str = "local-user") -> None:
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        with self._connect() as conn:
            conn.execute(
                "UPDATE autonomous_jobs SET approval='approved',updated_at=? "
                "WHERE id=? AND state IN ('READY','POLICY_REVIEW','SCHEDULED')",
                (_utc_now(), job_id),
            )
            self._event(conn, job_id, "approved", {"actor": actor})
        updated = self.get(job_id)
        if updated and updated["state"] == "READY":
            self.schedule(job_id)

    def schedule(self, job_id: str, publish_at: Optional[str] = None) -> str:
        target = publish_at or self.next_publish_slot()
        with self._connect() as conn:
            conn.execute(
                "UPDATE autonomous_jobs SET state='SCHEDULED',scheduled_at=?,updated_at=? "
                "WHERE id=? AND state='READY'",
                (target, _utc_now(), job_id),
            )
            self._event(conn, job_id, "scheduled", {"scheduled_at": target})
        return target

    def next_publish_slot(self, *, after: Optional[datetime] = None) -> str:
        start = after or datetime.now(timezone.utc)
        candidates: list[datetime] = []
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        for offset in range(0, 8):
            current_day = day + timedelta(days=offset)
            for value in self.config.publish_times_utc:
                try:
                    hour, minute = (int(part) for part in value.split(":", 1))
                except Exception:
                    continue
                candidate = current_day.replace(hour=hour, minute=minute)
                if candidate > start:
                    candidates.append(candidate)
        if not candidates:
            return (start + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return min(candidates).isoformat().replace("+00:00", "Z")

    def published_today(self) -> int:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM autonomous_jobs "
                "WHERE state IN ('PUBLISHED','ANALYZING','ANALYZED') AND published_at>=?",
                (start.isoformat().replace("+00:00", "Z"),),
            ).fetchone()
        return int(row["n"])

    def daily_cost(self) -> float:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(actual_cost_usd),0) AS cost FROM autonomous_jobs "
                "WHERE updated_at>=?",
                (start.isoformat().replace("+00:00", "Z"),),
            ).fetchone()
        return float(row["cost"] or 0.0)

    def recent_scripts(self, limit: int = 20) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT script FROM autonomous_jobs WHERE script<>'' "
                "ORDER BY created_at DESC LIMIT ?",
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [str(row["script"]) for row in rows if str(row["script"]).strip()]

    def event(self, job_id: Optional[str], name: str, details: Any = "") -> None:
        with self._connect() as conn:
            self._event(conn, job_id, name, details)

    def prune(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.retention_days)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,package_dir FROM autonomous_jobs "
                "WHERE state IN ('ANALYZED','FAILED') AND updated_at<?",
                (cutoff.isoformat().replace("+00:00", "Z"),),
            ).fetchall()
            for row in rows:
                conn.execute("DELETE FROM autonomous_jobs WHERE id=?", (row["id"],))
        for row in rows:
            package = Path(str(row["package_dir"] or ""))
            if package.exists() and package.is_dir():
                try:
                    import shutil
                    shutil.rmtree(package)
                except OSError:
                    logger.warning("failed to prune package %s", package)
        return len(rows)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _notify(config: AutonomousConfig, payload: Mapping[str, Any]) -> None:
    if not config.failure_webhook_url:
        return
    try:
        import requests
        requests.post(config.failure_webhook_url, json=dict(payload), timeout=8).raise_for_status()
    except Exception as exc:
        logger.warning("failure notification webhook failed: %s", exc)


def _discover_seed_topics(store: AutonomousStore, seeds: Sequence[str]) -> int:
    from .content_factory import generate_content_strategy, trend_research
    existing = {str(row["topic"]).strip().lower() for row in store.list_jobs(limit=200)}
    added = 0
    for seed in seeds:
        clean = str(seed).strip()
        if not clean:
            continue
        try:
            trend = trend_research(clean, limit=6)
            candidates = [
                str(item.get("title") or "").strip()
                for item in (trend.get("items") or [])
                if isinstance(item, Mapping)
            ]
            candidates.extend(
                str(item.get("angle") or item.get("topic") or "").strip()
                for item in generate_content_strategy(clean, count=5)
                if isinstance(item, Mapping)
            )
        except Exception as exc:
            logger.warning("topic discovery failed for %s: %s", clean, exc)
            candidates = []
        candidates = [item for item in candidates if item and item.lower() not in existing]
        for item in candidates[:3]:
            store.enqueue(item)
            existing.add(item.lower())
            added += 1
    return added


def _youtube_duration_seconds(video_path: Path) -> float:
    try:
        import json as _json
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return float((_json.loads(result.stdout).get("format") or {}).get("duration") or 0.0)
    except Exception:
        return 0.0


def _extract_package(package_dir: Path) -> tuple[dict[str, Any], str, Path]:
    metadata_path = package_dir / "upload" / "youtube_shorts" / "metadata.json"
    if not metadata_path.exists():
        metadata_path = package_dir / "upload" / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError("YouTube upload metadata was not produced")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    files = metadata.get("files") or {}
    video_rel = files.get("video") or "primary/final.mp4"
    video_path = package_dir / video_rel
    if not video_path.exists():
        candidates = [package_dir / "renders" / "youtube_shorts.mp4", package_dir / "primary" / "final.v3.mp4", package_dir / "primary" / "final.mp4"]
        video_path = next((candidate for candidate in candidates if candidate.exists()), video_path)
    script_path = package_dir / "primary" / "script.txt"
    script = script_path.read_text(encoding="utf-8", errors="replace") if script_path.exists() else ""
    return metadata, script, video_path


def _disclosure_from_package(metadata: Mapping[str, Any]) -> tuple[bool, bool]:
    explicit = metadata.get("ai_disclosure")
    if isinstance(explicit, Mapping):
        return bool(explicit.get("ai_generated", False)), bool(explicit.get("realistic_alteration", False))
    return False, False


class AutonomousManager:
    def __init__(self, config: Optional[AutonomousConfig] = None) -> None:
        self.config = config or AutonomousConfig.from_environment()
        self.store = AutonomousStore(self.config)

    def status(self) -> dict[str, Any]:
        jobs = self.store.list_jobs(limit=100)
        counts: dict[str, int] = {}
        for job in jobs:
            counts[str(job["state"])] = counts.get(str(job["state"]), 0) + 1
        return {
            "paused": self.store.setting("paused", "0") == "1",
            "emergency_stop": self.store.setting("emergency_stop", "0") == "1",
            "consecutive_failures": int(self.store.setting("consecutive_failures", "0") or 0),
            "published_today": self.store.published_today(),
            "daily_cost_usd": round(self.store.daily_cost(), 4),
            "queue_counts": counts,
            "config": {
                "max_videos_per_day": self.config.max_videos_per_day,
                "max_queue_size": self.config.max_queue_size,
                "max_consecutive_failures": self.config.max_consecutive_failures,
                "max_daily_cost_usd": self.config.max_daily_cost_usd,
                "autonomous_publish": self.config.autonomous_publish,
                "require_human_approval": self.config.require_human_approval,
            },
        }

    def pause(self, reason: str = "operator pause") -> None:
        self.store.set_setting("paused", "1")
        self.store.event(None, "paused", reason)

    def resume(self) -> None:
        self.store.set_setting("paused", "0")
        self.store.event(None, "resumed")
        
    def emergency_stop(self, reason: str = "emergency stop") -> None:
        self.store.set_setting("emergency_stop", "1")
        self.store.event(None, "emergency_stop", reason)

    def clear_emergency_stop(self) -> None:
        self.store.set_setting("emergency_stop", "0")
        self.store.event(None, "emergency_stop_cleared")

    def approve(self, job_id: str, actor: str = "local-user") -> None:
        self.store.approve(job_id, actor=actor)

    def enqueue(self, topic: str, **kwargs: Any) -> str:
        return self.store.enqueue(topic, **kwargs)

    def _guard_limits(self) -> None:
        if self.store.setting("emergency_stop", "0") == "1":
            raise RuntimeError("autonomous publisher emergency stop is active")
        if self.store.setting("paused", "0") == "1":
            raise RuntimeError("autonomous publisher is paused")
        if self.config.max_videos_per_day > 0 and self.store.published_today() >= self.config.max_videos_per_day:
            raise RuntimeError("daily upload limit reached")
        if self.config.max_daily_cost_usd > 0 and self.store.daily_cost() >= self.config.max_daily_cost_usd:
            raise RuntimeError("daily cost limit reached")
        if int(self.store.setting("consecutive_failures", "0") or 0) >= self.config.max_consecutive_failures:
            self.pause("automatic pause after repeated failures")
            raise RuntimeError("automatic failure circuit breaker is active")

    def _record_failure(self, job: Mapping[str, Any], exc: Exception, *, stage: str) -> None:
        retry_count = int(job.get("retry_count") or 0) + 1
        next_attempt: Optional[str] = None
        if retry_count <= self.config.max_retries:
            delay = min(
                self.config.retry_max_seconds,
                self.config.retry_base_seconds * (2 ** max(0, retry_count - 1)),
            )
            next_attempt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z")
            state = "FAILED"
        else:
            state = "POLICY_REVIEW"
        failures = int(self.store.setting("consecutive_failures", "0") or 0) + 1
        self.store.set_setting("consecutive_failures", str(failures))
        self.store.update(
            str(job["id"]),
            state=state,
            retry_count=retry_count,
            next_attempt_at=next_attempt,
            error=f"{stage}: {exc}",
        )
        self.store.event(str(job["id"]), "failed", {"stage": stage, "error": str(exc), "retry_count": retry_count})
        _notify(
            self.config,
            {
                "event": "autonomous_job_failed",
                "job_id": job["id"],
                "topic": job.get("topic"),
                "stage": stage,
                "error": str(exc),
                "retry_count": retry_count,
            },
        )
        if failures >= self.config.max_consecutive_failures:
            self.pause("automatic pause after repeated failures")
            _notify(
                self.config,
                {
                    "event": "autonomous_publisher_paused",
                    "reason": "repeated failures",
                    "consecutive_failures": failures,
                },
            )

    def _production_options(self, job: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "target_seconds": float(os.environ.get("AIVF_AUTONOMOUS_TARGET_SECONDS", "45")),
            "platforms": ["youtube_shorts"],
        }

    def produce_one(self) -> Optional[str]:
        self._guard_limits()
        job = self.store.claim_production()
        if not job:
            return None
        job_id = str(job["id"])
        package_dir = self.config.output_dir / "autonomous" / job_id
        try:
            options = self._production_options(job)
            result = run_complete_factory(
                None,
                str(job["topic"]),
                str(package_dir),
                target_seconds=options["target_seconds"],
                platforms=options["platforms"],
                experiment_history_path=str(self.config.state_dir / "learning_history.json"),
                auto_research=True,
                publish_youtube=False,
                skip_qc=False,
            )
            if result.get("status") != "complete":
                raise RuntimeError("; ".join(str(x) for x in result.get("errors") or ["production failed"]))
            metadata, script, video_path = _extract_package(package_dir)
            sanitized = sanitize_public_metadata(
                str(metadata.get("selected_title") or job["topic"]),
                str(metadata.get("description") or ""),
                metadata.get("tags") or [],
            )
            style = assess_package(
                script=script,
                title=sanitized["title"],
                description=sanitized["description"],
                recent_texts=self.store.recent_scripts(),
            )
            rights = metadata.get("media_rights") if isinstance(metadata.get("media_rights"), Mapping) else {}
            if style.publish_blocked:
                raise RuntimeError("human-quality guard blocked package: " + "; ".join(style.reasons[:6]))
            if rights.get("publish_blocked"):
                raise RuntimeError("rights gate blocked package; explicit evidence is required")
            if not video_path.exists():
                raise FileNotFoundError("final YouTube video missing")
            video_hash = _file_sha256(video_path)
            fingerprint = hashlib.sha256(
                (script + "\n" + sanitized["title"] + "\n" + video_hash).encode("utf-8")
            ).hexdigest()
            ai_generated, realistic_alteration = _disclosure_from_package(metadata)
            approval = "approved" if self.config.autonomous_publish and not self.config.require_human_approval else "pending"
            state = "SCHEDULED" if approval == "approved" else "READY"
            scheduled = self.store.next_publish_slot() if state == "SCHEDULED" else None
            self.store.update(
                job_id,
                state=state,
                package_dir=str(package_dir),
                title=sanitized["title"],
                script=script[:20000],
                fingerprint=fingerprint,
                video_sha256=video_hash,
                estimated_cost_usd=self.config.estimated_cost_per_video_usd,
                approval=approval,
                scheduled_at=scheduled,
                ai_generated=int(ai_generated),
                realistic_alteration=int(realistic_alteration),
                rights_json=json.dumps(rights, sort_keys=True),
                quality_json=json.dumps(style.to_dict(), sort_keys=True),
                error="",
            )
            self.store.set_setting("consecutive_failures", "0")
            self.store.event(job_id, "production_ready", {"style_score": style.score, "scheduled_at": scheduled})
            return job_id
        except Exception as exc:
            self._record_failure(job, exc, stage="production")
            return job_id

    def publish_one(self) -> Optional[str]:
        self._guard_limits()
        now = _utc_now()
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM autonomous_jobs WHERE state='SCHEDULED' "
                "AND approval='approved' AND scheduled_at<=? ORDER BY scheduled_at LIMIT 1",
                (now,),
            ).fetchone()
        if not row:
            return None
        job = dict(row)
        job_id = str(job["id"])
        self.store.update(job_id, state="UPLOADING")
        try:
            package = Path(str(job["package_dir"]))
            metadata, _script, video_path = _extract_package(package)
            if not video_path.exists():
                raise FileNotFoundError(video_path)
            public = sanitize_public_metadata(
                str(metadata.get("selected_title") or job["title"] or job["topic"]),
                str(metadata.get("description") or ""),
                metadata.get("tags") or [],
            )
            local_hash = str(job.get("video_sha256") or _file_sha256(video_path))
            result = upload_video(
                str(video_path),
                title=public["title"],
                description=public["description"],
                tags=public["tags"],
                privacy_status=os.environ.get("AIVF_YOUTUBE_PRIVACY", "public"),
                thumbnail_path=(
                    str(package / (metadata.get("files") or {}).get("thumbnail"))
                    if (metadata.get("files") or {}).get("thumbnail") else None
                ),
                caption_path=(
                    str(package / (metadata.get("files") or {}).get("captions_srt"))
                    if (metadata.get("files") or {}).get("captions_srt") else None
                ),
                client_secrets_path=os.environ.get("YOUTUBE_CLIENT_SECRETS"),
                token_path=os.environ.get("YOUTUBE_TOKEN_PATH"),
                ai_generated=bool(job.get("ai_generated")),
                realistic_alteration=bool(job.get("realistic_alteration")),
                disclosure_reviewed=True,
                idempotency_key=local_hash,
            )
            video_id = str(result["video_id"])
            self.store.update(
                job_id,
                state="PUBLISHED",
                video_id=video_id,
                actual_cost_usd=self.config.estimated_cost_per_video_usd,
                published_at=_utc_now(),
                error="",
            )
            self.store.set_setting("consecutive_failures", "0")
            self.store.event(job_id, "published", {"video_id": video_id, "deduplicated": bool(result.get("deduplicated"))})
            return video_id
        except Exception as exc:
            self._record_failure(job, exc, stage="publish")
            return job_id

    def analyze_one(self) -> Optional[str]:
        claim = self.store.claim_analysis()
        if not claim:
            return None
        job_id = str(claim["id"])
        try:
            video_id = str(claim["video_id"])
            client = os.environ.get("YOUTUBE_CLIENT_SECRETS")
            token = os.environ.get("YOUTUBE_TOKEN_PATH")
            stats = fetch_video_statistics(video_id, client_secrets_path=client, token_path=token)
            published = _parse_time(str(claim["published_at"]))
            start = published.date().isoformat()
            end = datetime.now(timezone.utc).date().isoformat()
            analytics = fetch_video_analytics(video_id, start_date=start, end_date=end, client_secrets_path=client, token_path=token)
            raw = (analytics.get("rows") or [{}])[0]
            views = float(raw.get("views", stats.get("statistics", {}).get("viewCount", 0)) or 0)
            likes = float(raw.get("likes", stats.get("statistics", {}).get("likeCount", 0)) or 0)
            comments = float(raw.get("comments", stats.get("statistics", {}).get("commentCount", 0)) or 0)
            avg_pct = float(raw.get("averageViewPercentage", 0.0) or 0.0)
            shares = float(raw.get("shares", 0.0) or 0.0)
            subscribers = float(raw.get("subscribersGained", 0.0) or 0.0)
            score = round(
                min(1.0, avg_pct / 100.0) * 0.50
                + min(1.0, likes / max(1.0, views) * 12.0) * 0.15
                + min(1.0, comments / max(1.0, views) * 30.0) * 0.10
                + min(1.0, shares / max(1.0, views) * 25.0) * 0.15
                + min(1.0, subscribers / max(1.0, views) * 50.0) * 0.10,
                4,
            )
            payload = {
                "views": views,
                "likes": likes,
                "comments": comments,
                "averageViewPercentage": avg_pct,
                "shares": shares,
                "subscribersGained": subscribers,
                "video_title": (stats.get("snippet") or {}).get("title"),
                "analytics": analytics,
                "video": stats,
            }
            with self.store._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO autonomous_analytics(job_id,video_id,observed_at,metrics,score) "
                    "VALUES(?,?,?,?,?)",
                    (job_id, video_id, _utc_now(), json.dumps(payload, sort_keys=True, default=str), score),
                )
            feedback = FeedbackStore(self.config.state_dir / "feedback.sqlite")
            feedback.add(
                video_id,
                "youtube_shorts",
                "Autonomous",
                _utc_now(),
                {
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "retention": avg_pct / 100.0,
                    "completion": avg_pct / 100.0,
                    "share": shares / max(1.0, views),
                    "save": 0.0,
                    "subscriber_rate": subscribers / max(1.0, views),
                },
                metadata={"job_id": job_id, "score": score},
            )
            self.store.update(job_id, state="ANALYZED", analytics_json=json.dumps(payload, sort_keys=True, default=str))
            self.store.set_setting("consecutive_failures", "0")
            self.store.event(job_id, "analytics_recorded", {"score": score})
            return job_id
        except Exception as exc:
            self._record_failure(claim, exc, stage="analytics")
            return job_id

    def generate_learning_profile(self) -> dict[str, Any]:
        feedback = FeedbackStore(self.config.state_dir / "feedback.sqlite")
        rows = feedback.aggregate(platform="youtube_shorts")
        observations: list[PerformanceObservation] = []
        for key, group in rows.items():
            for _ in range(int(group.get("samples", 0))):
                metrics = group.get("means") or {}
                observations.append(
                    PerformanceObservation(
                        video_id=f"aggregate-{key}-{len(observations)}",
                        hook_score=0.5,
                        retention_rate=float(metrics.get("retention", 0.0) or 0.0),
                        completion_rate=float(metrics.get("completion", 0.0) or 0.0),
                        rewatch_rate=0.0,
                        share_rate=float(metrics.get("share", 0.0) or 0.0),
                        save_rate=float(metrics.get("save", 0.0) or 0.0),
                        edit_type=str(key.split(":", 1)[1]),
                    )
                )
        model = ChannelPerformanceModel(observations)
        rec = model.recommend()
        result = {
            "sample_size": model.sample_size,
            "baseline": dict(model.baseline()),
            "edit_type_lift": model.edit_type_lift(),
            "recommendation": {
                "priority": rec.priority,
                "confidence": rec.confidence,
                "changes": list(rec.changes),
                "evidence_count": rec.evidence_count,
                "rationale": rec.rationale,
            },
            "generated_at": _utc_now(),
        }
        target = self.config.state_dir / "channel-learning.json"
        target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return result

    def run_once(self, *, seed_topics: Optional[Sequence[str]] = None) -> dict[str, Any]:
        started = time.time()
        result: dict[str, Any] = {"produced": None, "published": None, "analyzed": None, "discovered": 0}
        if self.store.setting("emergency_stop", "0") == "1" or self.store.setting("paused", "0") == "1":
            result["paused"] = True
            return result
        if seed_topics:
            try:
                result["discovered"] = _discover_seed_topics(self.store, seed_topics)
            except Exception as exc:
                _notify(self.config, {"event": "topic_discovery_failed", "error": str(exc)})
        try:
            result["produced"] = self.produce_one()
        except Exception:
            logger.exception("autonomous production cycle failed")
        try:
            result["published"] = self.publish_one()
        except Exception:
            logger.exception("autonomous publishing cycle failed")
        try:
            result["analyzed"] = self.analyze_one()
        except Exception:
            logger.exception("autonomous analytics cycle failed")
        try:
            result["learning"] = self.generate_learning_profile()
        except Exception:
            logger.exception("autonomous learning update failed")
        self.store.prune()
        result["elapsed_seconds"] = round(time.time() - started, 3)
        return result

    def run_forever(self, *, seed_topics: Optional[Sequence[str]] = None) -> None:
        logger.info("autonomous YouTube loop started")
        while True:
            try:
                self.run_once(seed_topics=seed_topics)
            except Exception:
                logger.exception("autonomous cycle crashed")
            time.sleep(self.config.scheduler_interval_seconds)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Edit Factory autonomous YouTube operating loop")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("run-once")
    sub.add_parser("daemon")
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("topic")
    enqueue.add_argument("--publish-at", default=None)
    enqueue.add_argument("--no-publish", action="store_true")
    approve = sub.add_parser("approve")
    approve.add_argument("job_id")
    pause = sub.add_parser("pause")
    pause.add_argument("reason", nargs="?", default="operator pause")
    sub.add_parser("resume")
    stop = sub.add_parser("emergency-stop")
    stop.add_argument("reason", nargs="?", default="emergency stop")
    sub.add_parser("clear-emergency-stop")
    list_cmd = sub.add_parser("queue")
    list_cmd.add_argument("--limit", type=int, default=50)
    sub.add_parser("learn")
    args = parser.parse_args(list(argv) if argv is not None else None)

    manager = AutonomousManager()
    if args.command == "status":
        print(json.dumps(manager.status(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "enqueue":
        job_id = manager.enqueue(args.topic, scheduled_at=args.publish_at, publish_requested=not args.no_publish)
        print(json.dumps({"job_id": job_id, "status": manager.store.get(job_id)}, indent=2, ensure_ascii=False))
        return 0
    if args.command == "approve":
        manager.approve(args.job_id)
        return 0
    if args.command == "pause":
        manager.pause(args.reason)
        return 0
    if args.command == "resume":
        manager.resume()
        return 0
    if args.command == "emergency-stop":
        manager.emergency_stop(args.reason)
        return 0
    if args.command == "clear-emergency-stop":
        manager.clear_emergency_stop()
        return 0
    if args.command == "queue":
        print(json.dumps(manager.store.list_jobs(limit=args.limit), indent=2, ensure_ascii=False, default=str))
        return 0
    if args.command == "learn":
        print(json.dumps(manager.generate_learning_profile(), indent=2, ensure_ascii=False, default=str))
        return 0

    seeds = [item.strip() for item in os.environ.get("AIVF_TOPIC_SEEDS", "").split("|") if item.strip()]
    if args.command == "run-once":
        print(json.dumps(manager.run_once(seed_topics=seeds), indent=2, ensure_ascii=False, default=str))
        return 0
    manager.run_forever(seed_topics=seeds)
    return 0


__all__ = ["AutonomousConfig", "AutonomousStore", "AutonomousManager", "main"]
