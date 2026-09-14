"""Thumbnail variant selection backed by the existing feedback history."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Tuple


def rank_thumbnail_variants(root_dir: str, topic: str) -> Dict[int, Tuple[float, int]]:
    """Return smoothed mean engagement and sample count for variants 1-3."""
    buckets = defaultdict(list)
    history_path = Path(root_dir) / "feedback_history.json"
    if history_path.exists():
        try:
            records = json.loads(history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            records = []
    else:
        records = []

    for record in records:
        if not isinstance(record, dict):
            continue
        features = record.get("features") or {}
        style = str(features.get("thumbnail_style", ""))
        if not style.startswith("variant_"):
            continue
        try:
            variant = int(style.split("_", 1)[1])
            score = float(record.get("engagement_score", 0.0))
        except (ValueError, TypeError, IndexError):
            continue
        if variant in (1, 2, 3):
            # Give exact-topic history a modest weight without letting it exceed 1.0.
            exact_topic = str(features.get("topic", "")).strip().lower() == topic.strip().lower()
            weight = 1.25 if exact_topic else 1.0
            buckets[variant].append((max(0.0, min(1.0, score)), weight))

    ranked: Dict[int, Tuple[float, int]] = {}
    for variant in (1, 2, 3):
        values = buckets.get(variant, [])
        weighted_total = sum(value * weight for value, weight in values)
        weight_total = sum(weight for _, weight in values)
        # Beta(1,1)-style smoothing keeps unseen variants at a neutral 0.5.
        ranked[variant] = ((weighted_total + 0.5) / (weight_total + 1.0), len(values))
    return ranked


def choose_thumbnail_variant(root_dir: str, topic: str) -> int:
    """Pick the highest-scoring observed variant; prefer more evidence on ties."""
    ranked = rank_thumbnail_variants(root_dir, topic)
    return max(ranked, key=lambda variant: (ranked[variant][0], ranked[variant][1], -variant))
