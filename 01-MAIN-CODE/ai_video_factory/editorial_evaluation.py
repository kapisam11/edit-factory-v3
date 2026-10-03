"""Evidence-aware editorial evaluation, provenance and human feedback primitives for V3."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class EvidenceKind(str, Enum):
    DETERMINISTIC = "deterministic"
    HEURISTIC = "heuristic"
    MODEL = "model"
    EXTERNAL = "external"
    HUMAN = "human"


@dataclass(frozen=True)
class Evidence:
    kind: EvidenceKind | str
    method: str
    confidence: float
    version: str = "unknown"
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("evidence confidence must be between 0 and 1")
        if not str(self.method).strip():
            raise ValueError("evidence method is required")
        object.__setattr__(self, "confidence", round(confidence, 3))
        object.__setattr__(self, "kind", EvidenceKind(self.kind).value)
        object.__setattr__(self, "details", dict(self.details))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Assessment:
    """A quality/value estimate with uncertainty kept separate from the value."""
    value: float
    confidence: float
    evidence: tuple[Evidence, ...] = ()
    method: str = "heuristic"
    method_version: str = "1.0.0"

    def __post_init__(self) -> None:
        value = float(self.value)
        confidence = float(self.confidence)
        if not 0.0 <= value <= 1.0:
            raise ValueError("assessment value must be between 0 and 1")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("assessment confidence must be between 0 and 1")
        if not str(self.method).strip() or not str(self.method_version).strip():
            raise ValueError("assessment method and method_version are required")
        object.__setattr__(self, "value", round(value, 3))
        object.__setattr__(self, "confidence", round(confidence, 3))
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass(frozen=True)
class EditorialDecision:
    operation: str
    timestamp: float
    reason: str
    confidence: float
    evidence: tuple[Evidence, ...] = ()
    source: str = "v3"
    decision_version: str = "1.0.0"
    policy_version: str = "1.0.0"
    config_hash: str = ""
    source_hash: str = ""
    planner_version: str = "3.0.0"

    def __post_init__(self) -> None:
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("decision confidence must be between 0 and 1")
        if not str(self.operation).strip():
            raise ValueError("decision operation is required")
        if not str(self.reason).strip():
            raise ValueError("decision reason is required")
        object.__setattr__(self, "timestamp", round(float(self.timestamp), 3))
        object.__setattr__(self, "confidence", round(confidence, 3))
        object.__setattr__(self, "evidence", tuple(self.evidence))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = [item.to_dict() for item in self.evidence]
        return payload


@dataclass(frozen=True)
class EditorialEvaluation:
    hook: float
    pacing: float
    coherence: float
    payoff: float
    caption_quality: float
    overall: float
    reviewer: str = ""
    notes: str = ""
    evaluated_at: str = ""
    case_id: str = ""

    def __post_init__(self) -> None:
        for name in ("hook", "pacing", "coherence", "payoff", "caption_quality", "overall"):
            value = float(getattr(self, name))
            if not 1.0 <= value <= 5.0:
                raise ValueError(f"{name} must be between 1 and 5")
            object.__setattr__(self, name, round(value, 2))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    source: str
    genre: str
    expected_duration: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HumanDecisionFeedback:
    decision_id: str
    action: str
    reason_code: str
    severity: int
    actor: str
    timestamp: str
    pipeline_version: str

    def __post_init__(self) -> None:
        allowed = {"KEEP", "REJECT", "MOVE", "WEAK", "STRONG", "WRONG_REASON"}
        action = str(self.action).strip().upper()
        if action not in allowed:
            raise ValueError(f"unsupported human feedback action: {self.action}")
        if not 0 <= int(self.severity) <= 3:
            raise ValueError("human feedback severity must be between 0 and 3")
        if not str(self.reason_code).strip():
            raise ValueError("human feedback reason_code is required")
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "severity", int(self.severity))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HumanOverride:
    decision_id: str
    original_operation: str
    final_operation: str
    reason: str
    actor: str
    timestamp: str
    pipeline_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decision_id(decision: EditorialDecision) -> str:
    canonical = json.dumps(decision.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def confidence_action(confidence: float) -> str:
    value = float(confidence)
    if value >= 0.85:
        return "automatic"
    if value >= 0.60:
        return "automatic_with_flag"
    return "conservative"


def build_retention_decisions(
    events: Sequence[Mapping[str, Any]],
    *,
    clip_purposes: Mapping[float, str] | None = None,
    version: str = "1.0.0",
    policy_version: str = "1.0.0",
    config_hash: str = "",
    source_hash: str = "",
    planner_version: str = "3.0.0",
) -> tuple[EditorialDecision, ...]:
    """Convert semantic retention events into auditable editorial decisions."""
    reasons = {
        "beat drop": ("BEAT_DROP", 0.90),
        "clip": ("SCENE_CHANGE", 0.88),
        "zoom": ("ATTENTION_RISK", 0.72),
        "text": ("COMPREHENSION_SUPPORT", 0.78),
        "motion": ("VISUAL_CHANGE", 0.70),
        "angle": ("ATTENTION_RISK", 0.64),
        "do_nothing": ("LOW_CONFIDENCE", 0.40),
    }
    decisions: list[EditorialDecision] = []
    purposes = clip_purposes or {}
    for raw in events:
        if not isinstance(raw, Mapping):
            continue
        timestamp = float(raw.get("time", 0.0))
        kind = str(raw.get("kind", "")).strip().lower()
        reason = str(raw.get("reason", "")).strip().upper() or reasons.get(kind, ("ATTENTION_RISK", 0.55))[0]
        default_confidence = reasons.get(kind, ("ATTENTION_RISK", 0.55))[1]
        confidence = float(raw.get("confidence", default_confidence))
        importance = float(raw.get("semantic_importance", 0.5))
        purpose = purposes.get(round(timestamp, 3), "")
        operation = "DO_NOTHING" if confidence < 0.60 or kind == "do_nothing" else kind.upper()
        evidence = (
            Evidence(
                EvidenceKind.HEURISTIC,
                method="v3 semantic retention policy",
                confidence=confidence,
                version=version,
                details={"kind": kind, "purpose": purpose, "semantic_importance": round(importance, 3)},
            ),
        )
        decisions.append(
            EditorialDecision(
                operation=operation,
                timestamp=timestamp,
                reason=reason,
                confidence=confidence,
                evidence=evidence,
                decision_version=version,
                policy_version=policy_version,
                config_hash=config_hash,
                source_hash=source_hash,
                planner_version=planner_version,
            )
        )
    return tuple(decisions)


def summarize_editorial_evidence(decisions: Sequence[EditorialDecision]) -> dict[str, Any]:
    if not decisions:
        return {"count": 0, "average_confidence": 0.0, "low_confidence": 0, "operations": {}, "do_nothing_rate": 0.0}
    operations: dict[str, int] = {}
    for decision in decisions:
        operations[decision.operation] = operations.get(decision.operation, 0) + 1
    return {
        "count": len(decisions),
        "average_confidence": round(sum(d.confidence for d in decisions) / len(decisions), 3),
        "low_confidence": sum(d.confidence < 0.60 for d in decisions),
        "operations": operations,
        "do_nothing_rate": round(operations.get("DO_NOTHING", 0) / len(decisions), 3),
    }


__all__ = [
    "Assessment",
    "EditorialDecision",
    "EditorialEvaluation",
    "EvaluationCase",
    "Evidence",
    "EvidenceKind",
    "HumanDecisionFeedback",
    "HumanOverride",
    "build_retention_decisions",
    "confidence_action",
    "decision_id",
    "summarize_editorial_evidence",
]
