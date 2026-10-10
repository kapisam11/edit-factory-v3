"""Durable, provider-neutral performance and editorial feedback storage."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .editorial_evaluation import EditorialEvaluation, HumanDecisionFeedback, HumanOverride


@dataclass(frozen=True)
class FeedbackRecord:
    video_id: str
    platform: str
    edit_type: str
    observed_at: str
    metrics: dict[str, float]
    metadata: dict[str, Any]


class FeedbackStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(Path(path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS feedback "
                "(video_id TEXT PRIMARY KEY, platform TEXT NOT NULL, edit_type TEXT NOT NULL, "
                "observed_at TEXT NOT NULL, metrics TEXT NOT NULL, metadata TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_platform_edit ON feedback(platform,edit_type)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS editorial_evaluations "
                "(case_id TEXT PRIMARY KEY, evaluated_at TEXT NOT NULL, reviewer TEXT NOT NULL, "
                "scores TEXT NOT NULL, notes TEXT NOT NULL, metadata TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS editorial_decision_feedback "
                "(decision_id TEXT NOT NULL, action TEXT NOT NULL, reason_code TEXT NOT NULL, "
                "severity INTEGER NOT NULL, actor TEXT NOT NULL, timestamp TEXT NOT NULL, "
                "pipeline_version TEXT NOT NULL, PRIMARY KEY(decision_id, action, reason_code, timestamp))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS editorial_overrides "
                "(decision_id TEXT PRIMARY KEY, original_operation TEXT NOT NULL, final_operation TEXT NOT NULL, "
                "reason TEXT NOT NULL, actor TEXT NOT NULL, timestamp TEXT NOT NULL, pipeline_version TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def record(self, record: FeedbackRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO feedback(video_id,platform,edit_type,observed_at,metrics,metadata) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET "
                "platform=excluded.platform,edit_type=excluded.edit_type,observed_at=excluded.observed_at,"
                "metrics=excluded.metrics,metadata=excluded.metadata",
                (
                    record.video_id,
                    record.platform,
                    record.edit_type,
                    record.observed_at,
                    json.dumps(record.metrics, sort_keys=True),
                    json.dumps(record.metadata, sort_keys=True),
                ),
            )

    def add(
        self,
        video_id: str,
        platform: str,
        edit_type: str,
        observed_at: str,
        metrics: Mapping[str, float],
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        values = {str(k): float(v) for k, v in metrics.items()}
        self.record(FeedbackRecord(video_id, platform, edit_type, observed_at, values, dict(metadata or {})))

    def record_editorial_evaluation(
        self,
        evaluation: EditorialEvaluation,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not evaluation.case_id:
            raise ValueError("editorial evaluation requires case_id")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO editorial_evaluations(case_id,evaluated_at,reviewer,scores,notes,metadata) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET "
                "evaluated_at=excluded.evaluated_at,reviewer=excluded.reviewer,scores=excluded.scores,"
                "notes=excluded.notes,metadata=excluded.metadata",
                (
                    evaluation.case_id,
                    evaluation.evaluated_at,
                    evaluation.reviewer,
                    json.dumps({
                        "hook": evaluation.hook,
                        "pacing": evaluation.pacing,
                        "coherence": evaluation.coherence,
                        "payoff": evaluation.payoff,
                        "caption_quality": evaluation.caption_quality,
                        "overall": evaluation.overall,
                    }, sort_keys=True),
                    evaluation.notes,
                    json.dumps(dict(metadata or {}), sort_keys=True),
                ),
            )

    def record_human_feedback(self, feedback: HumanDecisionFeedback) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO editorial_decision_feedback("
                "decision_id,action,reason_code,severity,actor,timestamp,pipeline_version"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    feedback.decision_id,
                    feedback.action,
                    feedback.reason_code,
                    feedback.severity,
                    feedback.actor,
                    feedback.timestamp,
                    feedback.pipeline_version,
                ),
            )

    def record_override(self, override: HumanOverride) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO editorial_overrides(decision_id,original_operation,final_operation,reason,actor,timestamp,pipeline_version) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(decision_id) DO UPDATE SET "
                "original_operation=excluded.original_operation,final_operation=excluded.final_operation,"
                "reason=excluded.reason,actor=excluded.actor,timestamp=excluded.timestamp,pipeline_version=excluded.pipeline_version",
                (
                    override.decision_id,
                    override.original_operation,
                    override.final_operation,
                    override.reason,
                    override.actor,
                    override.timestamp,
                    override.pipeline_version,
                ),
            )

    def editorial_summary(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute("SELECT scores FROM editorial_evaluations").fetchall()
            overrides = conn.execute("SELECT original_operation,final_operation FROM editorial_overrides").fetchall()
            feedback = conn.execute("SELECT action,reason_code,severity FROM editorial_decision_feedback").fetchall()
        feedback_actions: dict[str, int] = {}
        for action, _reason, _severity in feedback:
            feedback_actions[str(action)] = feedback_actions.get(str(action), 0) + 1
        if not rows:
            return {
                "samples": 0,
                "means": {},
                "overrides": {"samples": len(overrides)},
                "human_feedback": {
                    "samples": len(feedback),
                    "actions": feedback_actions,
                },
            }
        buckets = [json.loads(raw) for (raw,) in rows]
        keys = sorted(set().union(*(item.keys() for item in buckets)))
        means = {
            key: round(sum(float(item.get(key, 0.0)) for item in buckets) / len(buckets), 3)
            for key in keys
        }
        changed = sum(original != final for original, final in overrides)
        return {
            "samples": len(buckets),
            "means": means,
            "overrides": {
                "samples": len(overrides),
                "changed": changed,
                "change_rate": round(changed / len(overrides), 3) if overrides else 0.0,
            },
            "human_feedback": {
                "samples": len(feedback),
                "actions": feedback_actions,
            },
        }

    def aggregate(self, platform: str | None = None) -> dict[str, Any]:
        sql = "SELECT platform,edit_type,metrics FROM feedback"
        params: tuple[str, ...] = ()
        if platform:
            sql += " WHERE platform=?"
            params = (platform,)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for plat, edit, raw in rows:
            metrics = json.loads(raw)
            groups.setdefault((plat, edit), []).append(metrics)
        result: dict[str, Any] = {}
        for (plat, edit), bucket in groups.items():
            keys = set().union(*(item.keys() for item in bucket))
            means: dict[str, float] = {}
            for key in keys:
                # Metrics that are unavailable for a video are absent, not
                # zero. Average only observations that were actually measured.
                values = []
                for item in bucket:
                    if key not in item or item[key] is None:
                        continue
                    try:
                        value = float(item[key])
                    except (TypeError, ValueError):
                        continue
                    if value == value and abs(value) != float("inf"):
                        values.append(value)
                if values:
                    means[key] = round(sum(values) / len(values), 6)
            result[f"{plat}:{edit}"] = {
                "samples": len(bucket),
                "means": means,
            }
        return result


__all__ = ["FeedbackRecord", "FeedbackStore"]
