"""Empirical editorial evaluation utilities; no fabricated human ratings are bundled."""
from __future__ import annotations
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from .editorial_evaluation import EditorialEvaluation

DIMENSIONS = ("hook", "pacing", "coherence", "caption_quality", "payoff", "overall")

@dataclass(frozen=True)
class CorrelationResult:
    dimension: str
    samples: int
    spearman_rho: float | None
    mean_absolute_error: float | None
    status: str
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

def _rank(values: Sequence[float]) -> list[float]:
    ordered = sorted((float(value), index) for index, value in enumerate(values))
    ranks = [0.0] * len(values)
    i = 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][0] == ordered[i][0]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for _, index in ordered[i:j]:
            ranks[index] = rank
        i = j
    return ranks

def spearman_rho(predicted: Sequence[float], human: Sequence[float]) -> float | None:
    if len(predicted) != len(human) or len(predicted) < 3:
        return None
    a, b = _rank(predicted), _rank(human)
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    da = [x - mean_a for x in a]
    db = [x - mean_b for x in b]
    denom = (sum(x * x for x in da) * sum(x * x for x in db)) ** 0.5
    return None if denom == 0.0 else round(sum(x * y for x, y in zip(da, db)) / denom, 4)

def load_human_annotations(root: str | Path) -> list[EditorialEvaluation]:
    root = Path(root)
    if not root.is_dir():
        return []
    return [EditorialEvaluation(**json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(root.glob("*.json"))]

def build_correlation_report(
    predicted_by_case: Mapping[str, Mapping[str, float]],
    human_annotations: Sequence[EditorialEvaluation],
) -> dict[str, Any]:
    human_by_case = {item.case_id: item for item in human_annotations if item.case_id}
    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "samples": len(human_by_case),
        "dimensions": {},
        "warning": "Correlation is not reported until at least 3 matched human annotations exist.",
    }
    for dimension in DIMENSIONS:
        pairs = [
            (float(predicted_by_case[case_id][dimension]), float(getattr(annotation, dimension)))
            for case_id, annotation in human_by_case.items()
            if case_id in predicted_by_case and dimension in predicted_by_case[case_id]
        ]
        predicted, human = zip(*pairs) if pairs else ((), ())
        mae = round(sum(abs(a - b) for a, b in pairs) / len(pairs), 4) if pairs else None
        rho = spearman_rho(predicted, human)
        report["dimensions"][dimension] = CorrelationResult(
            dimension, len(pairs), rho, mae,
            "measured" if rho is not None else "insufficient_samples",
        ).to_dict()
    return report

def write_report(path: str | Path, report: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(dict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8")

__all__ = ["DIMENSIONS", "CorrelationResult", "build_correlation_report", "load_human_annotations", "spearman_rho", "write_report"]
