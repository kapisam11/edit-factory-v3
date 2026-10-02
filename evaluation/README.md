# Editorial evaluation

This corpus measures editorial quality, not only pipeline validity.

Human ratings are deliberately not fabricated. Add real annotations under
`human_annotations/` before interpreting correlations.

## Layout

```
evaluation/
  cases/
  human_annotations/
  automated_results/
  reports/
```

Each human annotation uses a 1–5 scale for hook, pacing, coherence,
caption_quality, payoff and overall. Once at least three matched cases exist,
the evaluator reports Spearman rank correlation and mean absolute error.

A report with zero/insufficient annotations is a measurement-status report, not
evidence that the editor is good.
