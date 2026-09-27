# Architectural Findings — Edit Factory v3

## Confirmed high-risk findings

| Finding | File | Function/Class | Problem | Risk | Proposed change |
|---|---|---|---|---|---|
| Blueprint contract was only partially typed | `01-MAIN-CODE/ai_video_factory/v3_engine.py` | `V3Blueprint` | Core contract used loose strings/dicts and lacked typed QC/packaging/platform structures | Invalid creative state can cross into rendering | Add typed contract models, immutable collections, strict deserialization |
| Persisted blueprint was not independently revalidated | `01-MAIN-CODE/ai_video_factory/v3_pipeline.py` | `run_v3_pipeline` | JSON serialization drift could reach renderer | Rendering can consume stale/malformed state | Atomic write, read-back, deserialize and validate |
| AI script parser accepted arbitrary prose | `01-MAIN-CODE/ai_video_factory/production_pipeline.py` | `_normalize_model_script` | Prompt requested JSON but parser fell back to raw text | Untrusted model output can become production state | Strict schema validation with deterministic fallback |
| AI visual classifier accepted loosely shaped model data | `01-MAIN-CODE/ai_video_factory/visuals_fetcher.py` | `vet_with_model` | Unknown URLs/purposes and malformed confidence were not rejected | Invalid or unrelated media classification | Exact schema/source/confidence validation |
| Job update boundary did not enforce canonical transition rules | `01-MAIN-CODE/dashboard_store.py` | `DashboardStore.update_job*` | Storage could accept status changes independently of `job_state.py` | Impossible lifecycle states | Centralize transition validation in the storage boundary |
| Queue admission and worker claiming are separate concerns | dashboard queue/worker modules | dispatch/start flow | Queue inspection and process launch can race across callers | Duplicate worker execution | Atomic queued→running claim before process creation |
| Configuration access is fragmented | dashboard, media, provider modules | module-level env reads | Runtime defaults and limits are spread across modules | Inconsistent behavior and difficult testing | Introduce a small typed runtime settings boundary and migrate high-value paths |
| Heuristic metric formulas use inline coefficients | `v3_engine.py` | `_heuristic_metrics` | Formula weights have no named source/meaning | Hard to audit and tune safely | Named constants plus explicit heuristic metadata |

## Likely findings requiring targeted verification

- Some legacy dashboard modules still duplicate filesystem/database operations around the canonical storage boundary.
- Optional dependency handling is centralized for several dashboard capabilities, but some media/intelligence modules still perform direct lazy imports and environment reads.
- Some legacy media helpers call subprocesses directly instead of the shared FFmpeg wrapper. Each invocation needs contextual review rather than a blanket replacement.

## Possible findings

- A few large pipeline functions may still benefit from extraction into service-level stages after reliability changes are verified.
- Legacy compatibility routes may be safe to isolate further, but removal should wait for dependency/search evidence.

## Verification policy

Only confirmed findings are changed during the hardening work. Likely/possible findings are converted into tests or inspected further before refactoring.