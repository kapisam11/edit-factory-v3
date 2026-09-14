"""Closed-loop performance learning for Edit Factory v3."""
from __future__ import annotations
from dataclasses import dataclass, field
from statistics import mean
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

@dataclass(frozen=True)
class PerformanceObservation:
    video_id: str
    hook_score: float
    retention_rate: float
    completion_rate: float
    rewatch_rate: float
    share_rate: float
    save_rate: float = 0.0
    edit_type: str = "Storytelling"
    duration_seconds: float = 30.0
    def __post_init__(self) -> None:
        if not self.video_id.strip(): raise ValueError("video_id must not be empty")
        for name in ("hook_score", "retention_rate", "completion_rate", "rewatch_rate", "share_rate", "save_rate"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0: raise ValueError(f"{name} must be between 0 and 1")
        if self.duration_seconds <= 0: raise ValueError("duration_seconds must be positive")

@dataclass(frozen=True)
class LearningRecommendation:
    confidence: float
    priority: str
    changes: Sequence[str]
    evidence_count: int
    rationale: str

@dataclass
class ChannelPerformanceModel:
    observations: List[PerformanceObservation] = field(default_factory=list)
    def add(self, observation: PerformanceObservation) -> None: self.observations.append(observation)
    def extend(self, observations: Iterable[PerformanceObservation]) -> None:
        for observation in observations: self.add(observation)
    @property
    def sample_size(self) -> int: return len(self.observations)
    def baseline(self) -> Mapping[str, float]:
        if not self.observations: return {"retention": 0.0, "completion": 0.0, "rewatch": 0.0, "share": 0.0, "save": 0.0}
        return {"retention": mean(x.retention_rate for x in self.observations), "completion": mean(x.completion_rate for x in self.observations), "rewatch": mean(x.rewatch_rate for x in self.observations), "share": mean(x.share_rate for x in self.observations), "save": mean(x.save_rate for x in self.observations)}
    def edit_type_lift(self) -> Dict[str, float]:
        if not self.observations: return {}
        overall = mean(self._engagement(x) for x in self.observations); groups: Dict[str, List[float]] = {}
        for observation in self.observations: groups.setdefault(observation.edit_type, []).append(self._engagement(observation))
        return {kind: mean(values) - overall for kind, values in groups.items()}
    def recommend(self, *, current_edit_type: Optional[str] = None) -> LearningRecommendation:
        if self.sample_size < 3: return LearningRecommendation(0.0, "collect-data", ("Keep the V3 baseline strategy; collect at least 3 measured outcomes before adapting.",), self.sample_size, "Insufficient evidence for a stable channel-specific recommendation.")
        base = self.baseline(); changes: List[str] = []
        if base["retention"] < 0.55: changes.append("Tighten the opening and introduce a meaningful visual change every 1–2 seconds.")
        if base["completion"] < 0.45: changes.append("Shorten setup and move the payoff earlier while preserving the final beat.")
        if base["rewatch"] < 0.08: changes.append("Add a denser payoff or callback that rewards a second viewing.")
        if base["share"] < 0.03 and base["save"] < 0.03: changes.append("Increase emotional specificity and add a clearer share/save-worthy takeaway.")
        lifts = self.edit_type_lift()
        if current_edit_type and current_edit_type in lifts and lifts[current_edit_type] < -0.05:
            best = max(lifts, key=lifts.get)
            if best != current_edit_type and lifts[best] > 0.03: changes.append(f"Test {best} against {current_edit_type}; observed engagement lift favors {best}.")
        if not changes: changes.append("Keep the current V3 strategy and run controlled hook variants rather than broad edits.")
        return LearningRecommendation(min(0.95, 0.35 + 0.05 * self.sample_size), "optimize" if len(changes) > 1 else "refine", tuple(changes[:4]), self.sample_size, "Directional signals from observed channel outcomes, not causal guarantees.")
    @staticmethod
    def _engagement(o: PerformanceObservation) -> float:
        return 0.30*o.retention_rate + 0.25*o.completion_rate + 0.15*o.rewatch_rate + 0.15*o.share_rate + 0.15*o.save_rate
    def to_dict(self) -> Dict[str, object]:
        r = self.recommend()
        return {"sample_size": self.sample_size, "baseline": dict(self.baseline()), "edit_type_lift": self.edit_type_lift(), "recommendation": {"confidence": r.confidence, "priority": r.priority, "changes": list(r.changes), "evidence_count": r.evidence_count, "rationale": r.rationale}}
