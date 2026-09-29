"""Dependency-free, versioned online learning policy for production feedback."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


FEATURES = (
    "hook",
    "pacing",
    "visual_relevance",
    "caption_readability",
    "audio",
    "rights",
    "metadata",
    "retention",
    "ctr",
)


@dataclass
class LearningPolicy:
    version: int
    weights: dict[str, float]
    bias: float = 0.0

    @classmethod
    def default(cls) -> "LearningPolicy":
        return cls(
            version=1,
            weights={key: 1.0 / len(FEATURES) for key in FEATURES},
            bias=0.0,
        )

    def predict(self, features: Mapping[str, float]) -> float:
        total = self.bias
        for key, weight in self.weights.items():
            total += float(weight) * max(0.0, min(1.0, float(features.get(key, 0.0))))
        return max(0.0, min(1.0, total))

    def update(self, features: Mapping[str, float], target: float, *, learning_rate: float = 0.05) -> float:
        expected = max(0.0, min(1.0, float(target)))
        prediction = self.predict(features)
        error = expected - prediction
        rate = max(1e-4, min(0.5, float(learning_rate)))
        for key in self.weights:
            value = max(0.0, min(1.0, float(features.get(key, 0.0))))
            self.weights[key] = max(-2.0, min(2.0, self.weights[key] + rate * error * value))
        self.bias = max(-1.0, min(1.0, self.bias + rate * error))
        self.version += 1
        return prediction

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": 1, "version": self.version, "weights": dict(self.weights), "bias": self.bias}

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "LearningPolicy":
        weights = payload.get("weights")
        if not isinstance(weights, Mapping):
            return cls.default()
        normalized = {key: float(weights.get(key, 1.0 / len(FEATURES))) for key in FEATURES}
        return cls(int(payload.get("version", 1)), normalized, float(payload.get("bias", 0.0)))


def score_feedback(features: Mapping[str, float]) -> float:
    return LearningPolicy.default().predict(features)


def train_policy(records: Sequence[Mapping[str, object]], *, epochs: int = 3) -> LearningPolicy:
    policy = LearningPolicy.default()
    for _ in range(max(1, int(epochs))):
        for record in records:
            features = record.get("features") or {}
            if not isinstance(features, Mapping):
                continue
            target = float(record.get("target", record.get("label", 0.0)))
            policy.update({str(k): float(v) for k, v in features.items()}, target)
    return policy


def save_policy(policy: LearningPolicy, path: str | Path) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(policy.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(target)


def load_policy(path: str | Path) -> LearningPolicy:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("learning policy must be an object")
    return LearningPolicy.from_dict(payload)


__all__ = ["FEATURES", "LearningPolicy", "load_policy", "save_policy", "score_feedback", "train_policy"]
