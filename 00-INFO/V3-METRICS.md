# V3 metric semantics

The V3 `retention_heuristic`, `completion_heuristic`, `rewatch_heuristic`, and `shareability_heuristic` values are **heuristic editorial scores**, not predictions of platform analytics.

They are deterministic quality indicators built from the blueprint's hook, pacing, emotion and structural QC. They must never be presented as probabilities or expected viewer percentages.

A future learned model may replace these formulas only after calibration against real published-video observations. Until then, the explicit `heuristic` designation is part of the API contract.


## Current heuristic weights

The current coefficients are explicit engineering choices so they can be reviewed and changed deliberately:

- Retention: hook 0.35, pacing 0.30, quality 0.20, emotion 0.15.
- Completion: quality 0.45, pacing 0.30, hook 0.25.
- Rewatch: hook 0.40, emotion 0.35, quality 0.25.
- Shareability: emotion 0.50, quality 0.30, hook 0.20.

These weights are not trained from platform outcome data. The implementation records `method=heuristic`, `confidence=low`, and `calibration_status=uncalibrated` in the V3 blueprint.


## Evidence semantics

The performance metrics are deterministic editorial heuristics, not predictions.
Each value is accompanied by method/calibration metadata in V3 score artifacts.
Creative quality and confidence are separate quantities.

The compatibility aliases `*_score` remain readable for existing callers, but new
artifacts should use the explicit `*_heuristic` names.

Technical validity is `null` in a planning-only blueprint and becomes a measured
render-validation result only after the rendered artifact passes its technical checks.
