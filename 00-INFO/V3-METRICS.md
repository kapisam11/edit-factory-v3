# V3 metric semantics

The V3 `retention_score`, `completion_score`, `rewatch_score`, and `shareability_score` values are **heuristic editorial scores**, not predictions of platform analytics.

They are deterministic quality indicators built from the blueprint's hook, pacing, emotion and structural QC. They must never be presented as probabilities or expected viewer percentages.

A future learned model may replace these formulas only after calibration against real published-video observations. Until then, the explicit `heuristic` designation is part of the API contract.
