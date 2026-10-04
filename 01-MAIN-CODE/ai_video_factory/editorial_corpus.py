"""Persistent empirical evaluation corpus for V3 editorial and performance models.

The corpus never fabricates labels. Correlation/error reports remain explicitly
"insufficient_samples" until real matched annotations/outcomes are recorded.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping

from .editorial_benchmark import DIMENSIONS, spearman_rho

PERFORMANCE_DIMENSIONS = ("retention_heuristic", "completion_heuristic", "rewatch_heuristic", "shareability_heuristic")


@dataclass(frozen=True)
class CorpusCase:
    case_id: str
    video_id: str
    blueprint_version: str
    renderer_version: str
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PerformanceOutcome:
    case_id: str
    retention: float | None = None
    completion: float | None = None
    rewatches: float | None = None
    shares: float | None = None


class EditorialCorpus:
    def __init__(self, path: str | Path) -> None:
        self.path = str(Path(path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cases("
                "case_id TEXT PRIMARY KEY, video_id TEXT NOT NULL, "
                "blueprint_version TEXT NOT NULL, renderer_version TEXT NOT NULL, metadata TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS predictions("
                "case_id TEXT NOT NULL, dimension TEXT NOT NULL, value REAL NOT NULL, "
                "confidence REAL NOT NULL, PRIMARY KEY(case_id,dimension))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS outcomes("
                "case_id TEXT PRIMARY KEY, retention REAL, completion REAL, rewatches REAL, shares REAL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS human_annotations("
                "case_id TEXT PRIMARY KEY, scores TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def add_case(self, case: CorpusCase) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO cases(case_id,video_id,blueprint_version,renderer_version,metadata) "
                "VALUES(?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET "
                "video_id=excluded.video_id, blueprint_version=excluded.blueprint_version, "
                "renderer_version=excluded.renderer_version, metadata=excluded.metadata",
                (
                    case.case_id, case.video_id, case.blueprint_version,
                    case.renderer_version, json.dumps(dict(case.metadata), sort_keys=True),
                ),
            )

    def add_blueprint_predictions(
        self,
        case_id: str,
        metrics: Mapping[str, float],
        *,
        confidence: float,
    ) -> None:
        """Capture existing heuristic metrics without fabricating human labels."""
        for dimension in PERFORMANCE_DIMENSIONS:
            if dimension in metrics:
                self.add_prediction(
                    case_id,
                    dimension,
                    float(metrics[dimension]) / 100.0,
                    confidence,
                )

    def add_blueprint_predictions(
        self,
        case_id: str,
        metrics: Mapping[str, float],
        *,
        confidence: float,
    ) -> None:
        """Store pipeline heuristics; human ratings and outcomes remain independent."""
        for dimension in PERFORMANCE_DIMENSIONS:
            if dimension in metrics:
                self.add_prediction(
                    case_id,
                    dimension,
                    float(metrics[dimension]) / 100.0,
                    confidence,
                )
        for dimension in DIMENSIONS:
            if dimension in metrics:
                self.add_prediction(
                    case_id,
                    dimension,
                    float(metrics[dimension]) / 100.0,
                    confidence,
                )

    def add_prediction(self, case_id: str, dimension: str, value: float, confidence: float) -> None:
        if dimension not in DIMENSIONS and dimension not in PERFORMANCE_DIMENSIONS:
            raise ValueError(f"unsupported evaluation dimension: {dimension}")
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError("prediction value must be 0..1")
        if not 0.0 <= float(confidence) <= 1.0:
            raise ValueError("prediction confidence must be 0..1")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO predictions(case_id,dimension,value,confidence) VALUES(?,?,?,?) "
                "ON CONFLICT(case_id,dimension) DO UPDATE SET value=excluded.value,confidence=excluded.confidence",
                (case_id, dimension, float(value), float(confidence)),
            )

    def add_human_annotation(self, case_id: str, scores: Mapping[str, float]) -> None:
        missing = set(DIMENSIONS) - set(scores)
        if missing:
            raise ValueError("human annotation is missing dimensions: " + ", ".join(sorted(missing)))
        checked = {name: float(scores[name]) for name in DIMENSIONS}
        if any(not 1.0 <= value <= 5.0 for value in checked.values()):
            raise ValueError("human editorial ratings must be 1..5")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO human_annotations(case_id,scores) VALUES(?,?) "
                "ON CONFLICT(case_id) DO UPDATE SET scores=excluded.scores",
                (case_id, json.dumps(checked, sort_keys=True)),
            )

    def add_outcome(self, outcome: PerformanceOutcome) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO outcomes(case_id,retention,completion,rewatches,shares) VALUES(?,?,?,?,?) "
                "ON CONFLICT(case_id) DO UPDATE SET retention=excluded.retention,completion=excluded.completion,"
                "rewatches=excluded.rewatches,shares=excluded.shares",
                (
                    outcome.case_id, outcome.retention, outcome.completion,
                    outcome.rewatches, outcome.shares,
                ),
            )

    def correlation_report(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT p.case_id,p.dimension,p.value,h.scores "
                "FROM predictions p JOIN human_annotations h ON h.case_id=p.case_id"
            ).fetchall()
        by_dimension: dict[str, tuple[list[float], list[float]]] = {
            name: ([], []) for name in DIMENSIONS
        }
        for case_id, dimension, value, raw_scores in rows:
            # Performance predictions share the same storage table but do not
            # have 1..5 human editorial ratings. Keep them for performance_report
            # and exclude them from the editorial correlation calculation.
            if dimension not in DIMENSIONS:
                continue
            scores = json.loads(raw_scores)
            if dimension not in scores:
                continue
            by_dimension[dimension][0].append(float(value))
            by_dimension[dimension][1].append(float(scores[dimension]) / 5.0)
        dimensions: dict[str, Any] = {}
        for dimension in DIMENSIONS:
            predicted, human = by_dimension[dimension]
            mae = round(
                sum(abs(a - b) for a, b in zip(predicted, human)) / len(predicted), 4
            ) if predicted else None
            rho = spearman_rho(predicted, human)
            dimensions[dimension] = {
                "samples": len(predicted),
                "spearman_rho": rho,
                "mean_absolute_error": mae,
                "status": "measured" if rho is not None else "insufficient_samples",
            }
        return {
            "schema_version": "1.0.0",
            "samples": len({row[0] for row in rows}),
            "dimensions": dimensions,
            "warning": "No human or outcome labels are fabricated by this corpus.",
        }

    def performance_report(self) -> dict[str, Any]:
        mapping = {
            "retention_heuristic": "retention",
            "completion_heuristic": "completion",
            "rewatch_heuristic": "rewatches",
            "shareability_heuristic": "shares",
        }
        report: dict[str, Any] = {"schema_version": "1.0.0", "metrics": {}}
        with self._connect() as conn:
            for predicted_name, outcome_name in mapping.items():
                rows = conn.execute(
                    "SELECT p.value,o." + outcome_name + " FROM predictions p "
                    "JOIN outcomes o ON o.case_id=p.case_id WHERE p.dimension=? AND o." + outcome_name + " IS NOT NULL",
                    (predicted_name,),
                ).fetchall()
                pairs = [(float(a), float(b)) for a, b in rows]
                report["metrics"][predicted_name] = {
                    "samples": len(pairs),
                    "spearman_rho": spearman_rho([a for a, _ in pairs], [b for _, b in pairs]),
                    "mean_absolute_error": round(
                        sum(abs(a - b) for a, b in pairs) / len(pairs), 4
                    ) if pairs else None,
                    "status": "measured" if len(pairs) >= 3 else "insufficient_samples",
                }
        return report

    def export(self, path: str | Path) -> None:
        payload = {
            "correlation": self.correlation_report(),
            "performance": self.performance_report(),
        }
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


__all__ = ["CorpusCase", "EditorialCorpus", "PerformanceOutcome", "PERFORMANCE_DIMENSIONS"]
