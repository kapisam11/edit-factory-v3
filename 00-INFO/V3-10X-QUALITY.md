# Edit Factory V3 — 10/10 quality bar

V3 is considered production-ready when every generated artifact is traceable, validated, reproducible, and measurable.

## Closed-loop architecture

`idea -> emotion director -> hook/timeline -> media production -> QC -> package -> publish -> measured outcomes -> channel model -> next blueprint`

The new `performance_learning` module is deliberately offline-safe. It accepts measured outcomes from any provider, calculates channel baselines and edit-type lift, and emits bounded recommendations. It does not claim causality and does not make platform API calls itself.

## Engineering bar

- Preserve the existing renderer as the single media execution path.
- Validate all creative contracts before rendering.
- Keep compatibility imports stable.
- Never persist credentials in observations or recommendations.
- Fail closed on invalid metrics.
- Require evidence before changing channel strategy.
- Prefer controlled hook experiments over broad unexplained changes.
- Keep recommendations explainable and bounded.
- Test both the no-data and learning paths.

## Remaining external boundary

Actual platform analytics ingestion, publishing, and provider-specific media models require authenticated integrations and are intentionally kept outside this offline core. The core exposes stable contracts for those adapters rather than faking live data.
