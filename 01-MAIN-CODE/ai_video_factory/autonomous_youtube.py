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
from .human_style_guard import assess_package, assess_topic_diversity, sanitize_public_metadata
from .performance_learning import ChannelPerformanceModel, PerformanceObservation
from .youtube_publisher import fetch_video_analytics, fetch_video_statistics, upload_video

logger = logging.getLogger(__name__)

AUTONOMOUS_SCHEMA_VERSION = 5


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
    estimated_cost_per_video_usd: float = 1.0
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
    backup_interval_hours: float = 24.0
    backup_retention_count: int = 7
    channel_niche: str = ""
    min_topic_variation: float = 0.72
    stale_schedule_days: int = 14
    stale_job_minutes: int = 120
    require_factual_review: bool = True
    allow_public_autopublish: bool = False
    auto_disclosure_policy_acknowledged: bool = False

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
            estimated_cost_per_video_usd=max(0.0, float(os.environ.get("AIVF_ESTIMATED_COST_PER_VIDEO_USD", "1.0"))),
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
            backup_interval_hours=max(1.0, float(os.environ.get("AIVF_AUTOMATION_BACKUP_INTERVAL_HOURS", "24"))),
            backup_retention_count=max(1, int(os.environ.get("AIVF_AUTOMATION_BACKUP_RETENTION_COUNT", "7"))),
            channel_niche=os.environ.get("AIVF_CHANNEL_NICHE", "").strip(),
            min_topic_variation=max(0.5, min(0.95, float(os.environ.get("AIVF_MIN_TOPIC_VARIATION", "0.72")))),
            stale_schedule_days=max(1, int(os.environ.get("AIVF_STALE_SCHEDULE_DAYS", "14"))),
            stale_job_minutes=max(10, int(os.environ.get("AIVF_AUTONOMOUS_STALE_JOB_MINUTES", "120"))),
            require_factual_review=os.environ.get("AIVF_AUTONOMOUS_REQUIRE_FACT_REVIEW", "1").strip() == "1",
            allow_public_autopublish=os.environ.get("AIVF_AUTONOMOUS_ALLOW_PUBLIC", "0").strip() == "1",
            auto_disclosure_policy_acknowledged=os.environ.get(
                "AIVF_AUTONOMOUS_DISCLOSURE_POLICY_ACKNOWLEDGED", "0"
            ).strip() == "1",
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _normalize_scheduled_at(value: Optional[str]) -> Optional[str]:
    if value is None or not str(value).strip():
        return None
    parsed = _parse_time(str(value).strip()).replace(microsecond=0)
    return parsed.isoformat().replace("+00:00", "Z")


def _parse_json(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return default


def _fact_review_pending(review: Any, *, required: bool) -> bool:
    """Fail closed when required factual-review evidence is absent or incomplete."""
    if not required:
        return False
    if not isinstance(review, Mapping):
        return True
    claims = review.get("claims_to_verify")
    if not isinstance(claims, Sequence) or isinstance(claims, (str, bytes)):
        return True
    if "research_available" not in review:
        return True
    return bool(claims) or not bool(review.get("research_available"))


def _rights_review_pending(rights: Any) -> bool:
    """Missing, blocked, or malformed source-rights metadata cannot green-light publishing."""
    if not isinstance(rights, Mapping):
        return True
    status = str(rights.get("status") or "")
    if bool(rights.get("publish_blocked")) or status not in {"cleared", "not_declared"}:
        return True
    if bool(rights.get("requires_explicit_declaration")) and status != "cleared":
        return True
    return False


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
            current = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            if current > AUTONOMOUS_SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported autonomous queue schema {current}; "
                    f"maximum supported is {AUTONOMOUS_SCHEMA_VERSION}"
                )

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
                    metadata_sha256 TEXT,
                    script TEXT NOT NULL DEFAULT '',
                    estimated_cost_usd REAL NOT NULL DEFAULT 0,
                    actual_cost_usd REAL NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    retry_stage TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT 'youtube_shorts',
                    options_json TEXT NOT NULL DEFAULT '{}',
                    experiment_family TEXT NOT NULL DEFAULT '',
                    title_variant INTEGER NOT NULL DEFAULT 1,
                    thumbnail_variant INTEGER NOT NULL DEFAULT 1,
                    next_attempt_at TEXT,
                    approval TEXT NOT NULL DEFAULT 'pending',
                    publish_requested INTEGER NOT NULL DEFAULT 0,
                    ai_generated INTEGER NOT NULL DEFAULT 0,
                    realistic_alteration INTEGER NOT NULL DEFAULT 0,
                    disclosure_reviewed INTEGER NOT NULL DEFAULT 0,
                    rights_json TEXT NOT NULL DEFAULT '{}',
                    quality_json TEXT NOT NULL DEFAULT '{}',
                    analytics_json TEXT NOT NULL DEFAULT '{}',
                    published_at TEXT,
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
                    score REAL NOT NULL DEFAULT 0,
                    revenue_usd REAL NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_auto_analytics_video_time
                    ON autonomous_analytics(video_id, observed_at);
                CREATE TABLE IF NOT EXISTS autonomous_cost_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    amount_usd REAL NOT NULL CHECK(amount_usd >= 0),
                    source TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '{}',
                    event_key TEXT NOT NULL UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_auto_cost_events_time
                    ON autonomous_cost_events(occurred_at);
                """
            )

            # Version 0 was used by the first autonomous loop build.  Treat it
            # as an upgradeable legacy schema and bring it forward through one
            # explicit, named migration rather than scattering ALTER TABLE calls
            # across normal startup logic.
            if current == 0:
                job_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(autonomous_jobs)").fetchall()}
                analytics_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(autonomous_analytics)").fetchall()}
                migrations: tuple[tuple[str, str, set[str]], ...] = (
                    (
                        "autonomous_jobs_v2",
                        "autonomous_jobs",
                        {"retry_stage", "platform", "options_json", "experiment_family", "title_variant", "thumbnail_variant", "published_at"},
                    ),
                    ("autonomous_analytics_v2", "autonomous_analytics", {"revenue_usd"}),
                )
                for migration_name, table_name, required in migrations:
                    existing = job_columns if table_name == "autonomous_jobs" else analytics_columns
                    definitions = {
                        "retry_stage": "TEXT NOT NULL DEFAULT ''",
                        "platform": "TEXT NOT NULL DEFAULT 'youtube_shorts'",
                        "options_json": "TEXT NOT NULL DEFAULT '{}'",
                        "experiment_family": "TEXT NOT NULL DEFAULT ''",
                        "title_variant": "INTEGER NOT NULL DEFAULT 1",
                        "thumbnail_variant": "INTEGER NOT NULL DEFAULT 1",
                        "published_at": "TEXT",
                        "revenue_usd": "REAL NOT NULL DEFAULT 0",
                    }
                    for column in sorted(required - existing):
                        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {definitions[column]}")
                current = 1

            if current < 2:
                # v2 formalizes the analytics revenue column and the publication
                # experiment metadata used by the learning loop.
                job_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(autonomous_jobs)").fetchall()}
                analytics_columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(autonomous_analytics)").fetchall()}
                for column, definition in {
                    "experiment_family": "TEXT NOT NULL DEFAULT ''",
                    "title_variant": "INTEGER NOT NULL DEFAULT 1",
                    "thumbnail_variant": "INTEGER NOT NULL DEFAULT 1",
                    "published_at": "TEXT",
                }.items():
                    if column not in job_columns:
                        conn.execute(f"ALTER TABLE autonomous_jobs ADD COLUMN {column} {definition}")
                if "revenue_usd" not in analytics_columns:
                    conn.execute("ALTER TABLE autonomous_analytics ADD COLUMN revenue_usd REAL NOT NULL DEFAULT 0")
                current = 2

            if current < 3:
                # v3 records a spend estimate for each production attempt instead
                # of inferring daily cost from updated_at (which changes for
                # uploads, retries, and analytics). event_key makes this
                # migration safe to retry if startup is interrupted.
                legacy_costs = conn.execute(
                    "SELECT id, updated_at, actual_cost_usd FROM autonomous_jobs "
                    "WHERE actual_cost_usd > 0"
                ).fetchall()
                for legacy in legacy_costs:
                    event_key = f"legacy:{legacy['id']}:{legacy['updated_at']}"
                    conn.execute(
                        """
                        INSERT INTO autonomous_cost_events(
                            job_id, occurred_at, amount_usd, source, details, event_key
                        ) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(event_key) DO NOTHING
                        """,
                        (
                            str(legacy["id"]),
                            str(legacy["updated_at"]),
                            float(legacy["actual_cost_usd"]),
                            "legacy_estimate",
                            "{}",
                            event_key,
                        ),
                    )
                current = 3

            if current < 4:
                job_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(autonomous_jobs)").fetchall()
                }
                if "disclosure_reviewed" not in job_columns:
                    conn.execute(
                        "ALTER TABLE autonomous_jobs ADD COLUMN disclosure_reviewed "
                        "INTEGER NOT NULL DEFAULT 0"
                    )
                # Videos without synthetic/AI flags need no disclosure decision.
                conn.execute(
                    "UPDATE autonomous_jobs SET disclosure_reviewed=1 "
                    "WHERE ai_generated=0 AND realistic_alteration=0"
                )
                # Never silently publish already-scheduled legacy AI jobs that
                # have no recorded disclosure review after this upgrade.
                conn.execute(
                    """
                    UPDATE autonomous_jobs
                    SET state='POLICY_REVIEW', approval='pending', scheduled_at=NULL,
                        error=CASE WHEN error='' THEN
                            'Disclosure review required after safety upgrade'
                            ELSE error END
                    WHERE state='SCHEDULED'
                      AND (ai_generated=1 OR realistic_alteration=1)
                      AND disclosure_reviewed=0
                    """
                )
                current = 4

            if current < 5:
                job_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(autonomous_jobs)").fetchall()
                }
                if "metadata_sha256" not in job_columns:
                    conn.execute("ALTER TABLE autonomous_jobs ADD COLUMN metadata_sha256 TEXT")
                # Old READY/SCHEDULED packages were not bound to the exact metadata
                # approved during review. Require a fresh review rather than trust
                # an unverifiable title/description/tags/rights snapshot.
                conn.execute(
                    """
                    UPDATE autonomous_jobs
                    SET state='POLICY_REVIEW', approval='pending', scheduled_at=NULL,
                        next_attempt_at=NULL,
                        error='Publication metadata integrity review required after upgrade',
                        updated_at=?
                    WHERE package_dir IS NOT NULL
                      AND state IN ('READY','POLICY_REVIEW','SCHEDULED')
                      AND (metadata_sha256 IS NULL OR metadata_sha256='')
                    """,
                    (_utc_now(),),
                )
                current = 5

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
            conn.execute(f"PRAGMA user_version={AUTONOMOUS_SCHEMA_VERSION}")

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

    def _select_experiment_variants(self, family: str, requested_title: int, requested_thumbnail: int) -> tuple[int, int]:
        """Prefer evidence-backed title/thumbnail variants while preserving exploration."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT title_variant, thumbnail_variant, analytics_json "
                "FROM autonomous_jobs WHERE experiment_family=? AND analytics_json<>'' "
                "ORDER BY updated_at DESC LIMIT 200",
                (family,),
            ).fetchall()
        title_stats: dict[int, list[float]] = {1: [], 2: [], 3: []}
        thumb_stats: dict[int, list[float]] = {1: [], 2: [], 3: []}
        for row in rows:
            try:
                payload = json.loads(str(row["analytics_json"] or "{}"))
                score = float(payload.get("score") or 0.0)
                title = int(row["title_variant"] or 1)
                thumb = int(row["thumbnail_variant"] or 1)
                if title in title_stats:
                    title_stats[title].append(score)
                if thumb in thumb_stats:
                    thumb_stats[thumb].append(score)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

        def choose(stats: dict[int, list[float]], fallback: int) -> int:
            unexplored = [variant for variant, values in stats.items() if not values]
            if unexplored:
                # Deterministic exploration prevents repeatedly picking the same
                # variant before there is enough evidence to optimize.
                return unexplored[(fallback - 1) % len(unexplored)]
            return max(
                stats,
                key=lambda variant: (
                    sum(stats[variant]) / max(1, len(stats[variant])),
                    len(stats[variant]),
                    -variant,
                ),
            )

        return choose(title_stats, requested_title), choose(thumb_stats, requested_thumbnail)

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
        scheduled_at = _normalize_scheduled_at(scheduled_at)
        if platform not in PLATFORM_LAYOUTS:
            raise ValueError(f"unsupported platform: {platform}")
        seed = f"{clean_topic}|{platform}|{scheduled_at or ''}|{json.dumps(dict(options or {}), sort_keys=True)}"
        job_id = "yt-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT COUNT(*) AS n FROM autonomous_jobs "
                "WHERE state IN ('IDEA','PRODUCING','READY','POLICY_REVIEW','SCHEDULED','UPLOADING','ANALYZING')"
            ).fetchone()["n"]
            if int(active) >= self.config.max_queue_size:
                raise RuntimeError("autonomous queue is full")
            conn.execute(
                """
                INSERT INTO autonomous_jobs(
                    id,topic,state,created_at,updated_at,priority,scheduled_at,
                    estimated_cost_usd,publish_requested,platform,options_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    priority=excluded.priority,
                    scheduled_at=excluded.scheduled_at,
                    publish_requested=excluded.publish_requested,
                    platform=excluded.platform,
                    options_json=excluded.options_json,
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
                    platform,
                    json.dumps(dict(options or {}), sort_keys=True),
                ),
            )
            experiment_family = hashlib.sha256(clean_topic.casefold().encode("utf-8")).hexdigest()[:12]
            requested_title_variant = (int(job_id[-2:], 16) % 3) + 1
            requested_thumbnail_variant = (int(job_id[-4:-2], 16) % 3) + 1
            title_variant, thumbnail_variant = self._select_experiment_variants(
                experiment_family,
                requested_title_variant,
                requested_thumbnail_variant,
            )
            conn.execute(
                "UPDATE autonomous_jobs SET experiment_family=?,title_variant=?,thumbnail_variant=? WHERE id=?",
                (experiment_family, title_variant, thumbnail_variant, job_id),
            )
            self._event(conn, job_id, "queued", {"topic": clean_topic, "platform": platform, "experiment_family": experiment_family, "title_variant": title_variant, "thumbnail_variant": thumbnail_variant})
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

    def _claim(
        self,
        states: Sequence[str],
        *,
        estimated_cost_usd: float = 0.0,
        max_daily_cost_usd: float = 0.0,
    ) -> Optional[dict[str, Any]]:
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
            estimate = max(0.0, float(estimated_cost_usd))
            if estimate > 0:
                day_start = _parse_time(now).replace(hour=0, minute=0, second=0, microsecond=0)
                day_start_text = day_start.isoformat().replace("+00:00", "Z")
                spent_row = conn.execute(
                    "SELECT COALESCE(SUM(amount_usd), 0) AS cost "
                    "FROM autonomous_cost_events WHERE occurred_at>=?",
                    (day_start_text,),
                ).fetchone()
                spent = float(spent_row["cost"] or 0.0)
                projected = spent + estimate
                if max_daily_cost_usd > 0 and projected >= max_daily_cost_usd:
                    conn.rollback()
                    raise RuntimeError("daily cost limit reached")
                conn.execute(
                    """
                    INSERT INTO autonomous_cost_events(
                        job_id, occurred_at, amount_usd, source, details, event_key
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        str(row["id"]),
                        now,
                        estimate,
                        "production_estimate",
                        json.dumps({"daily_cost_limit_usd": max_daily_cost_usd}, sort_keys=True),
                        secrets.token_hex(16),
                    ),
                )
            new_state = "PRODUCING" if row["state"] in {"IDEA", "FAILED"} else row["state"]
            conn.execute(
                "UPDATE autonomous_jobs SET state=?,updated_at=?,error='',"
                "actual_cost_usd=actual_cost_usd+? WHERE id=?",
                (new_state, now, estimate, row["id"]),
            )
            self._event(conn, row["id"], "claimed", {"from": row["state"], "to": new_state})
            conn.commit()
            updated = conn.execute("SELECT * FROM autonomous_jobs WHERE id=?", (row["id"],)).fetchone()
        return dict(updated) if updated else None

    def claim_production(
        self,
        *,
        estimated_cost_usd: float = 0.0,
        max_daily_cost_usd: float = 0.0,
    ) -> Optional[dict[str, Any]]:
        return self._claim(
            ("IDEA", "FAILED"),
            estimated_cost_usd=estimated_cost_usd,
            max_daily_cost_usd=max_daily_cost_usd,
        )

    def claim_analysis(self) -> Optional[dict[str, Any]]:
        now_dt = datetime.now(timezone.utc)
        cutoff = now_dt - timedelta(hours=self.config.analytics_after_hours)
        now = _utc_now()
        cutoff_iso = cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM autonomous_jobs WHERE state='PUBLISHED' "
                "AND published_at IS NOT NULL "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
                "AND published_at<=? ORDER BY published_at LIMIT 1",
                (now, cutoff_iso),
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
            "metadata_sha256",
            "script",
            "estimated_cost_usd",
            "actual_cost_usd",
            "retry_count",
            "retry_stage",
            "platform",
            "options_json",
            "experiment_family",
            "title_variant",
            "thumbnail_variant",
            "next_attempt_at",
            "approval",
            "publish_requested",
            "ai_generated",
            "realistic_alteration",
            "disclosure_reviewed",
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
                "UPDATE autonomous_jobs SET approval='approved',disclosure_reviewed=1,updated_at=? "
                "WHERE id=? AND state IN ('READY','POLICY_REVIEW','SCHEDULED')",
                (_utc_now(), job_id),
            )
            self._event(
                conn,
                job_id,
                "approved",
                {"actor": actor, "disclosure_reviewed": True},
            )
        updated = self.get(job_id)
        if updated and updated["state"] in {"READY", "POLICY_REVIEW"}:
            self.schedule(job_id)

    def schedule(self, job_id: str, publish_at: Optional[str] = None) -> str:
        target = _normalize_scheduled_at(publish_at) or self.next_publish_slot()
        with self._connect() as conn:
            conn.execute(
                "UPDATE autonomous_jobs SET state='SCHEDULED',scheduled_at=?,updated_at=? "
                "WHERE id=? AND state IN ('READY','POLICY_REVIEW') AND approval='approved'",
                (target, _utc_now(), job_id),
            )
            self._event(conn, job_id, "scheduled", {"scheduled_at": target})
        return target

    def claim_publish(self, now: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Claim one due upload atomically so two daemons cannot publish it twice."""
        current = now or _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM autonomous_jobs WHERE state='SCHEDULED' "
                "AND approval='approved' AND publish_requested=1 AND scheduled_at<=? "
                "AND ((ai_generated=0 AND realistic_alteration=0) OR disclosure_reviewed=1) "
                "ORDER BY priority DESC, scheduled_at, created_at LIMIT 1",
                (current,),
            ).fetchone()
            if not row:
                conn.commit()
                return None
            conn.execute(
                "UPDATE autonomous_jobs SET state='UPLOADING',updated_at=? WHERE id=? AND state='SCHEDULED'",
                (current, row["id"]),
            )
            if conn.total_changes != 1:
                conn.rollback()
                return None
            self._event(conn, row["id"], "upload_claimed", {"scheduled_at": row["scheduled_at"]})
            conn.commit()
            claimed = conn.execute("SELECT * FROM autonomous_jobs WHERE id=?", (row["id"],)).fetchone()
        return dict(claimed) if claimed else None

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
        """Return today's UTC production cost estimates from the append-only ledger."""
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_usd),0) AS cost FROM autonomous_cost_events "
                "WHERE occurred_at>=?",
                (start.isoformat().replace("+00:00", "Z"),),
            ).fetchone()
        return float(row["cost"] or 0.0)

    def daily_revenue(self) -> float:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(revenue_usd),0) AS revenue FROM autonomous_analytics WHERE observed_at>=?",
                (start.isoformat().replace("+00:00", "Z"),),
            ).fetchone()
        return float(row["revenue"] or 0.0)

    def recent_scripts(self, limit: int = 20) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT script FROM autonomous_jobs WHERE script<>'' "
                "ORDER BY created_at DESC LIMIT ?",
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [str(row["script"]) for row in rows if str(row["script"]).strip()]

    def recent_titles(self, limit: int = 30) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT title FROM autonomous_jobs WHERE title<>'' "
                "ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [str(row["title"]) for row in rows if str(row["title"]).strip()]

    def recent_topics(self, limit: int = 30, *, exclude_job_id: str = "") -> list[str]:
        with self._connect() as conn:
            if exclude_job_id:
                rows = conn.execute(
                    "SELECT topic FROM autonomous_jobs WHERE id<>? ORDER BY created_at DESC LIMIT ?",
                    (exclude_job_id, max(1, min(200, int(limit)))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT topic FROM autonomous_jobs ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(200, int(limit))),),
                ).fetchall()
        return [str(row["topic"]) for row in rows if str(row["topic"]).strip()]

    def topic_performance(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT j.topic,
                       COUNT(a.id) AS samples,
                       AVG(a.score) AS mean_score,
                       AVG(a.revenue_usd) AS mean_revenue
                FROM autonomous_jobs j
                JOIN autonomous_analytics a ON a.job_id=j.id
                GROUP BY j.topic
                ORDER BY mean_score DESC, samples DESC
                LIMIT ?
                """,
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return [
            {
                "topic": str(row["topic"]),
                "samples": int(row["samples"] or 0),
                "mean_score": round(float(row["mean_score"] or 0.0), 4),
                "mean_revenue_usd": round(float(row["mean_revenue"] or 0.0), 4),
            }
            for row in rows
        ]

    def event(self, job_id: Optional[str], name: str, details: Any = "") -> None:
        with self._connect() as conn:
            self._event(conn, job_id, name, details)

    def prune(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.retention_days)
        stale_cutoff = datetime.now(timezone.utc) - timedelta(days=self.config.stale_schedule_days)
        with self._connect() as conn:
            stale = conn.execute(
                "SELECT id FROM autonomous_jobs "
                "WHERE state='SCHEDULED' AND updated_at<?",
                (stale_cutoff.isoformat().replace("+00:00", "Z"),),
            ).fetchall()
            for row in stale:
                conn.execute(
                    "UPDATE autonomous_jobs SET state='POLICY_REVIEW',approval='pending',"
                    "next_attempt_at=NULL,error=?,updated_at=? WHERE id=?",
                    (
                        "scheduled content expired and requires review before publishing",
                        _utc_now(),
                        row["id"],
                    ),
                )
                self._event(conn, row["id"], "stale_schedule_review_required")

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
    from .human_style_guard import topic_similarity

    existing = {str(row["topic"]).strip().lower() for row in store.list_jobs(limit=200)}
    learned = store.topic_performance(limit=50)
    strong_topics = [str(item.get("topic") or "") for item in learned[:10]]
    weak_topics = [str(item.get("topic") or "") for item in learned[-10:]]
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
        filtered: list[tuple[float, str]] = []
        for item in candidates:
            if not item or item.lower() in existing:
                continue
            strong_fit = max((topic_similarity(item, reference) for reference in strong_topics), default=0.0)
            weak_fit = max((topic_similarity(item, reference) for reference in weak_topics), default=0.0)
            novelty = 1.0 - max(
                (topic_similarity(item, existing_topic) for existing_topic in existing),
                default=0.0,
            )
            score = 0.55 * strong_fit + 0.25 * novelty - 0.35 * weak_fit
            filtered.append((score, item))
        filtered.sort(key=lambda pair: (-pair[0], pair[1].casefold()))
        for _score, item in filtered[:3]:
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
        revenue = self.store.daily_revenue()
        cost = self.store.daily_cost()
        return {
            "paused": self.store.setting("paused", "0") == "1",
            "emergency_stop": self.store.setting("emergency_stop", "0") == "1",
            "consecutive_failures": int(self.store.setting("consecutive_failures", "0") or 0),
            "published_today": self.store.published_today(),
            "daily_cost_usd": round(cost, 4),
            "daily_estimated_revenue_usd": round(revenue, 4),
            "daily_roi": round((revenue - cost) / cost, 4) if cost > 0 else None,
            "queue_counts": counts,
            "config": {
                "max_videos_per_day": self.config.max_videos_per_day,
                "max_queue_size": self.config.max_queue_size,
                "max_consecutive_failures": self.config.max_consecutive_failures,
                "max_daily_cost_usd": self.config.max_daily_cost_usd,
                "autonomous_publish": self.config.autonomous_publish,
                "require_human_approval": self.config.require_human_approval,
                "channel_niche": self.config.channel_niche,
                "min_topic_variation": self.config.min_topic_variation,
                "stale_schedule_days": self.config.stale_schedule_days,
                "stale_job_minutes": self.config.stale_job_minutes,
                "require_factual_review": self.config.require_factual_review,
                "allow_public_autopublish": self.config.allow_public_autopublish,
                "auto_disclosure_policy_acknowledged": self.config.auto_disclosure_policy_acknowledged,
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

    def _raise_if_halted(self) -> None:
        if self.store.setting("emergency_stop", "0") == "1":
            raise RuntimeError("autonomous publisher emergency stop is active")
        if self.store.setting("paused", "0") == "1":
            raise RuntimeError("autonomous publisher is paused")

    def _guard_limits(self, *, reserve_production_cost: bool = True) -> None:
        if self.store.setting("emergency_stop", "0") == "1":
            raise RuntimeError("autonomous publisher emergency stop is active")
        if self.store.setting("paused", "0") == "1":
            raise RuntimeError("autonomous publisher is paused")
        if self.config.max_videos_per_day > 0 and self.store.published_today() >= self.config.max_videos_per_day:
            raise RuntimeError("daily upload limit reached")
        if self.config.max_daily_cost_usd > 0:
            daily_cost = self.store.daily_cost()
            projected = daily_cost + (
                self.config.estimated_cost_per_video_usd if reserve_production_cost else 0.0
            )
            if daily_cost >= self.config.max_daily_cost_usd or projected >= self.config.max_daily_cost_usd:
                raise RuntimeError("daily cost limit reached")
        if int(self.store.setting("consecutive_failures", "0") or 0) >= self.config.max_consecutive_failures:
            self.pause("automatic pause after repeated failures")
            raise RuntimeError("automatic failure circuit breaker is active")

    def _record_failure(self, job: Mapping[str, Any], exc: Exception, *, stage: str) -> None:
        error_text = str(exc)
        normalized_error = error_text.lower()
        auth_or_quota_failure = any(
            marker in normalized_error
            for marker in (
                "invalid_grant",
                "unauthorized",
                "authentication",
                "credentials",
                "quotaexceeded",
                "dailylimitexceeded",
                "quota exceeded",
            )
        )
        retry_count = int(job.get("retry_count") or 0) + 1
        next_attempt: Optional[str] = None
        if auth_or_quota_failure:
            state = "POLICY_REVIEW"
        elif retry_count <= self.config.max_retries:
            delay = min(
                self.config.retry_max_seconds,
                self.config.retry_base_seconds * (2 ** max(0, retry_count - 1)),
            )
            next_attempt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z")
            state = "FAILED" if stage == "production" else "SCHEDULED"
        else:
            state = "POLICY_REVIEW"

        if auth_or_quota_failure:
            next_attempt = None
        failures = int(self.store.setting("consecutive_failures", "0") or 0) + 1
        self.store.set_setting("consecutive_failures", str(failures))
        self.store.update(
            str(job["id"]),
            state=state,
            retry_count=retry_count,
            retry_stage=stage,
            next_attempt_at=next_attempt,
            error=f"{stage}: {exc}",
            actual_cost_usd=(
                float(job.get("actual_cost_usd") or 0.0)
                if stage != "production"
                else max(
                    float(job.get("actual_cost_usd") or 0.0),
                    self.config.estimated_cost_per_video_usd,
                )
            ),
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
        if auth_or_quota_failure:
            self.pause("YouTube authentication or quota failure requires operator action")
            _notify(
                self.config,
                {
                    "event": "autonomous_publish_blocked",
                    "reason": "youtube authentication or quota failure",
                    "job_id": job["id"],
                    "stage": stage,
                    "error": error_text[:500],
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
        options = _parse_json(str(job.get("options_json") or "{}"), {})
        if not isinstance(options, Mapping):
            options = {}
        platform = str(job.get("platform") or options.get("platform") or "youtube_shorts")
        return {
            "target_seconds": float(options.get("target_seconds") or os.environ.get("AIVF_AUTONOMOUS_TARGET_SECONDS", "45")),
            "platforms": [platform] if platform in PLATFORM_LAYOUTS else ["youtube_shorts"],
        }

    def produce_one(self) -> Optional[str]:
        self._guard_limits(reserve_production_cost=True)
        # Reservation and job claim share one BEGIN IMMEDIATE transaction so
        # competing single-host daemons cannot both spend the same budget headroom.
        job = self.store.claim_production(
            estimated_cost_usd=self.config.estimated_cost_per_video_usd,
            max_daily_cost_usd=self.config.max_daily_cost_usd,
        )
        if not job:
            return None
        job_id = str(job["id"])
        package_dir = self.config.output_dir / "autonomous" / job_id
        try:
            topic_guard = assess_topic_diversity(
                str(job["topic"]),
                self.store.recent_topics(exclude_job_id=job_id),
                min_similarity=self.config.min_topic_variation,
            )
            if topic_guard["blocked"]:
                raise RuntimeError(
                    "topic diversity guard blocked candidate: "
                    f"similarity {topic_guard['max_recent_topic_similarity']:.2f} "
                    f">= {topic_guard['threshold']:.2f}"
                )
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
            self._raise_if_halted()
            metadata, script, video_path = _extract_package(package_dir)
            complete_manifest: dict[str, Any] = {}
            manifest_path = package_dir / "complete_factory_manifest.json"
            if manifest_path.exists():
                try:
                    manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if isinstance(manifest_value, dict):
                        complete_manifest = manifest_value
                except (OSError, ValueError):
                    complete_manifest = {}
            fact_review = complete_manifest.get("fact_check_review")
            if not isinstance(fact_review, Mapping):
                fact_review = {}
            primary_meta: dict[str, Any] = {}
            primary_metadata_path = package_dir / "primary" / "metadata.json"
            if primary_metadata_path.exists():
                try:
                    loaded = json.loads(primary_metadata_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        primary_meta = loaded
                except (OSError, ValueError):
                    primary_meta = {}
            pipeline_ai_generated = bool(primary_meta.get("model_backed", False))
            pipeline_realistic_alteration = bool(primary_meta.get("altered_media", False))
            title_candidates_value = metadata.get("title_candidates")
            title_candidates: list[str] = (
                [str(value).strip() for value in title_candidates_value if str(value).strip()]
                if isinstance(title_candidates_value, Sequence) and not isinstance(title_candidates_value, (str, bytes))
                else []
            )
            title_variant = max(1, int(job.get("title_variant") or 1))
            raw_selected_title = title_candidates[(title_variant - 1) % len(title_candidates)] if title_candidates else str(metadata.get("selected_title") or job.get("topic") or "")
            sanitized = sanitize_public_metadata(
                raw_selected_title,
                str(metadata.get("description") or ""),
                metadata.get("tags") or [],
            )
            style = assess_package(
                script=script,
                title=sanitized["title"],
                description=sanitized["description"],
                recent_texts=self.store.recent_scripts(),
                recent_titles=self.store.recent_titles(),
            )
            rights_value = metadata.get("media_rights")
            rights: Mapping[str, Any] = (
                rights_value if isinstance(rights_value, Mapping) else {}
            )
            if style.publish_blocked:
                raise RuntimeError("human-quality guard blocked package: " + "; ".join(style.reasons[:6]))
            if _rights_review_pending(rights_value):
                raise RuntimeError(
                    "rights gate blocked package; valid source-rights evidence is missing or unresolved"
                )
            # Required factual-review evidence must exist and identify the source
            # research status. Missing manifests do not count as a clean review.
            if not video_path.exists():
                raise FileNotFoundError("final YouTube video missing")
            video_hash = _file_sha256(video_path)
            fingerprint = hashlib.sha256(
                (script + "\n" + sanitized["title"] + "\n" + video_hash).encode("utf-8")
            ).hexdigest()
            package_ai_generated, package_realistic_alteration = _disclosure_from_package(metadata)
            ai_generated = pipeline_ai_generated or package_ai_generated
            realistic_alteration = pipeline_realistic_alteration or package_realistic_alteration
            fact_review_pending = _fact_review_pending(
                fact_review, required=self.config.require_factual_review
            )
            disclosure_review_required = ai_generated or realistic_alteration
            disclosure_reviewed = (
                not disclosure_review_required
                or self.config.auto_disclosure_policy_acknowledged
            )
            disclosure_review_pending = disclosure_review_required and not disclosure_reviewed
            approval = (
                "approved"
                if self.config.autonomous_publish
                and not self.config.require_human_approval
                and not fact_review_pending
                and not disclosure_review_pending
                else "pending"
            )
            state = (
                "SCHEDULED"
                if approval == "approved"
                else (
                    "POLICY_REVIEW"
                    if fact_review_pending or disclosure_review_pending
                    else "READY"
                )
            )
            scheduled = None
            if state == "SCHEDULED":
                requested_slot = str(job.get("scheduled_at") or "").strip()
                try:
                    scheduled = requested_slot if requested_slot and _parse_time(requested_slot) > datetime.now(timezone.utc) else self.store.next_publish_slot()
                except ValueError:
                    scheduled = self.store.next_publish_slot()
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
                experiment_family=str(job.get("experiment_family") or ""),
                title_variant=title_variant,
                ai_generated=int(ai_generated),
                realistic_alteration=int(realistic_alteration),
                disclosure_reviewed=int(disclosure_reviewed),
                rights_json=json.dumps(rights, sort_keys=True),
                quality_json=json.dumps(
                    {
                        **style.to_dict(),
                        "topic_diversity": topic_guard,
                        "factuality_review": fact_review,
                    },
                    sort_keys=True,
                ),
                error="",
            )
            self.store.set_setting("consecutive_failures", "0")
            self.store.event(job_id, "production_ready", {"style_score": style.score, "scheduled_at": scheduled, "experiment_family": str(job.get("experiment_family") or ""), "title_variant": title_variant})
            return job_id
        except Exception as exc:
            self._record_failure(job, exc, stage="production")
            return job_id

    def publish_one(self) -> Optional[str]:
        # Generation costs have already been reserved when the package was claimed.
        self._guard_limits(reserve_production_cost=False)
        self._raise_if_halted()
        privacy_status = os.environ.get("AIVF_YOUTUBE_PRIVACY", "private").strip().lower()
        if privacy_status not in {"private", "unlisted", "public"}:
            raise RuntimeError("invalid AIVF_YOUTUBE_PRIVACY value")
        if privacy_status == "public" and not self.config.allow_public_autopublish:
            raise RuntimeError(
                "public autonomous publishing is disabled; set "
                "AIVF_AUTONOMOUS_ALLOW_PUBLIC=1 after proving the pipeline"
            )
        job = self.store.claim_publish()
        if not job:
            return None
        job_id = str(job["id"])
        try:
            self._raise_if_halted()
        except Exception:
            # Return a claimed job to a safe review state instead of uploading
            # after an operator emergency stop was activated mid-cycle.
            self.store.update(
                job_id,
                state="POLICY_REVIEW",
                approval="pending",
                next_attempt_at=None,
                error="publication halted by operator stop",
            )
            self.store.event(job_id, "publish_halted")
            raise
        try:
            package = Path(str(job["package_dir"]))
            metadata, _script, video_path = _extract_package(package)
            if not video_path.exists():
                raise FileNotFoundError(video_path)
            title_candidates_value = metadata.get("title_candidates")
            title_candidates: list[str] = (
                [str(value).strip() for value in title_candidates_value if str(value).strip()]
                if isinstance(title_candidates_value, Sequence) and not isinstance(title_candidates_value, (str, bytes))
                else []
            )
            title_variant = max(1, int(job.get("title_variant") or 1))
            selected_title = (
                title_candidates[(title_variant - 1) % len(title_candidates)]
                if title_candidates
                else metadata.get("selected_title")
            )
            description_value = metadata.get("description")
            tags_value = metadata.get("tags")
            public_tags: Sequence[str] = (
                [str(tag) for tag in tags_value]
                if isinstance(tags_value, Sequence) and not isinstance(tags_value, (str, bytes))
                else []
            )
            public = sanitize_public_metadata(
                str(selected_title or job.get("title") or job.get("topic") or ""),
                str(description_value or ""),
                public_tags,
            )
            local_hash = str(job.get("video_sha256") or _file_sha256(video_path))
            files_value = metadata.get("files")
            package_files: Mapping[str, Any] = files_value if isinstance(files_value, Mapping) else {}
            thumbnail_rel = package_files.get("thumbnail")
            requested_thumb_variant = max(1, int(job.get("thumbnail_variant") or 1))
            thumbnail_candidates = sorted((package / "primary" / "thumbnails").glob("variant_*.png"))
            if thumbnail_candidates:
                selected_thumb = thumbnail_candidates[(requested_thumb_variant - 1) % len(thumbnail_candidates)]
                thumbnail_rel = str(selected_thumb.relative_to(package))
            captions_rel = package_files.get("captions_srt")
            result = upload_video(
                str(video_path),
                title=public["title"],
                description=public["description"],
                tags=public["tags"],
                privacy_status=privacy_status,
                thumbnail_path=str(package / str(thumbnail_rel)) if thumbnail_rel else None,
                caption_path=str(package / str(captions_rel)) if captions_rel else None,
                client_secrets_path=os.environ.get("YOUTUBE_CLIENT_SECRETS"),
                token_path=os.environ.get("YOUTUBE_TOKEN_PATH"),
                ai_generated=bool(job.get("ai_generated")),
                realistic_alteration=bool(job.get("realistic_alteration")),
                disclosure_reviewed=bool(job.get("disclosure_reviewed")),
                idempotency_key=local_hash,
            )
            video_id = str(result["video_id"])
            self.store.update(
                job_id,
                state="PUBLISHED",
                video_id=video_id,
                published_at=_utc_now(),
                error="",
            )
            self.store.set_setting("consecutive_failures", "0")
            self.store.event(job_id, "published", {"video_id": video_id, "deduplicated": bool(result.get("deduplicated")), "experiment_family": str(job.get("experiment_family") or ""), "title_variant": int(job.get("title_variant") or 1), "thumbnail_variant": int(job.get("thumbnail_variant") or 1)})
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
            revenue = float(raw.get("estimatedRevenue", 0.0) or 0.0)
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
                "estimatedRevenue": revenue,
                "experiment_family": str(claim.get("experiment_family") or ""),
                "title_variant": int(claim.get("title_variant") or 1),
                "thumbnail_variant": int(claim.get("thumbnail_variant") or 1),
                "video_title": (stats.get("snippet") or {}).get("title"),
                "analytics": analytics,
                "video": stats,
            }
            with self.store._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO autonomous_analytics(job_id,video_id,observed_at,metrics,score,revenue_usd) "
                    "VALUES(?,?,?,?,?,?)",
                    (job_id, video_id, _utc_now(), json.dumps(payload, sort_keys=True, default=str), score, revenue),
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
            retry = int(claim.get("retry_count") or 0) + 1
            delay = min(self.config.retry_max_seconds, self.config.retry_base_seconds * (2 ** max(0, retry - 1)))
            next_attempt = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z")
            self.store.update(
                job_id,
                state="PUBLISHED",
                retry_count=retry,
                retry_stage="analytics",
                next_attempt_at=next_attempt,
                error=f"analytics: {exc}",
            )
            _notify(self.config, {"event": "autonomous_analytics_retry", "job_id": job_id, "error": str(exc), "retry_count": retry})
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
        experiment_groups: dict[str, list[float]] = {}
        with self.store._connect() as conn:
            db_rows = conn.execute("SELECT analytics_json FROM autonomous_jobs WHERE state='ANALYZED' ORDER BY updated_at DESC LIMIT 500").fetchall()
        for row in db_rows:
            analytics = _parse_json(str(row["analytics_json"] or "{}"), {})
            family = str(analytics.get("experiment_family") or "")
            if not family:
                continue
            variant = f"title{int(analytics.get('title_variant') or 1)}-thumb{int(analytics.get('thumbnail_variant') or 1)}"
            score = float(analytics.get("score") or 0.0)
            experiment_groups.setdefault(f"{family}:{variant}", []).append(score)
        experiment_summary = {key: {"samples": len(values), "mean_score": round(sum(values) / len(values), 4)} for key, values in experiment_groups.items() if values}
        topic_performance = self.store.topic_performance(limit=50)
        top_topics = topic_performance[:10]
        result = {
            "sample_size": model.sample_size,
            "baseline": dict(model.baseline()),
            "edit_type_lift": model.edit_type_lift(),
            "experiments": experiment_summary,
            "topic_performance": topic_performance,
            "top_topics": top_topics,
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

    def _write_config_snapshot(self) -> str:
        target = self.config.output_dir / "autonomous-config.json"
        payload = {
            "version": 1,
            "captured_at": _utc_now(),
            "limits": {
                "max_videos_per_day": self.config.max_videos_per_day,
                "max_queue_size": self.config.max_queue_size,
                "max_consecutive_failures": self.config.max_consecutive_failures,
                "max_daily_cost_usd": self.config.max_daily_cost_usd,
                "estimated_cost_per_video_usd": self.config.estimated_cost_per_video_usd,
                "max_retries": self.config.max_retries,
                "retry_base_seconds": self.config.retry_base_seconds,
                "retry_max_seconds": self.config.retry_max_seconds,
            },
            "publishing": {
                "publish_times_utc": list(self.config.publish_times_utc),
                "autonomous_publish": self.config.autonomous_publish,
                "require_human_approval": self.config.require_human_approval,
                "require_factual_review": self.config.require_factual_review,
                "allow_public_autopublish": self.config.allow_public_autopublish,
                "auto_disclosure_policy_acknowledged": self.config.auto_disclosure_policy_acknowledged,
            },
            "editorial": {
                "channel_niche": self.config.channel_niche,
                "min_topic_variation": self.config.min_topic_variation,
                "stale_schedule_days": self.config.stale_schedule_days,
            },
        }
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return str(target)

    def _maybe_backup(self) -> Optional[str]:
        last = float(self.store.setting("last_backup_epoch", "0") or 0)
        now_epoch = time.time()
        if now_epoch - last < self.config.backup_interval_hours * 3600.0:
            return None
        from .backup_restore import create_backup, verify_backup
        self._write_config_snapshot()
        backup_dir = self.config.state_dir / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = backup_dir / ("autonomous-" + stamp + ".tar.gz")
        archive = create_backup(
            db_path=self.config.queue_db,
            backup_path=target,
            knowledge_root=os.environ.get("AIVF_KNOWLEDGE_ROOT") or None,
            output_root=self.config.output_dir,
        )
        verification = verify_backup(archive)
        if not bool(verification.get("ok")):
            raise RuntimeError("automatic automation-state backup verification failed")
        self.store.set_setting("last_backup_epoch", str(now_epoch))
        backups = sorted(backup_dir.glob("autonomous-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in backups[self.config.backup_retention_count:]:
            try:
                stale.unlink()
            except OSError:
                logger.warning("failed to prune stale automation backup %s", stale)
        self.store.event(None, "backup_created", {"archive": str(archive), "verified_files": verification.get("verified_files")})
        return str(archive)
    def run_once(self, *, seed_topics: Optional[Sequence[str]] = None) -> dict[str, Any]:
        started = time.time()
        result: dict[str, Any] = {"produced": None, "published": None, "analyzed": None, "discovered": 0, "backup": None}
        try:
            result["backup"] = self._maybe_backup()
        except Exception as exc:
            logger.exception("automatic backup failed")
            _notify(self.config, {"event": "autonomous_backup_failed", "error": str(exc)})
        if self.store.setting("emergency_stop", "0") == "1" or self.store.setting("paused", "0") == "1":
            result["paused"] = True
            return result
        effective_seeds = [str(item).strip() for item in (seed_topics or []) if str(item).strip()]
        if not effective_seeds and self.config.channel_niche:
            effective_seeds = [self.config.channel_niche]
        if effective_seeds:
            try:
                result["discovered"] = _discover_seed_topics(self.store, effective_seeds)
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
