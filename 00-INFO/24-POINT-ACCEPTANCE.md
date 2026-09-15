# V3 hostile-review acceptance matrix

This document converts the hostile review into explicit engineering acceptance criteria.

| # | Finding | Acceptance condition | Evidence |
|---:|---|---|---|
| 1 | Blueprint/render mismatch | V3 timeline uses blueprint beat count as source of truth | `edit_planner.build_timeline` + strict tests |
| 2 | Exact duration | Segment renderer pads short sources and post-render QC normalizes the final artifact | `render_engine.render_segment`, `v3_quality.normalize_duration` |
| 3 | Retention only advisory | Retention events survive into render QC and rendered visual-change sampling | `v3_quality.strict_render_check` |
| 4 | Directive/effect seam | Every V3 motion/transition supported by strategy has an FFmpeg mapping | `effects_engine`, `test_v3_effect_mapping.py` |
| 5 | QC naming | Editorial QC is structural and render QC is separately evidenced | `v3_engine`, `v3_quality` |
| 6 | Fake predictive metrics | Scores are explicitly documented as heuristics; no predictive guarantee is claimed | capability registry + PR boundary statement |
| 7 | Shallow emotion detection | Optional semantic encoder with deterministic lexical fallback | `v3_semantics.py` |
| 8 | Weak audience awareness | Audience is an explicit planning input and can bias semantic planning; it is not treated as authoritative | `V3Config.audience`, `analyze_core_idea` |
| 9 | Cyclic edit strategy | Each edit type has an explicit strategy and deterministic fallback rather than one shared plan | `EDIT_STRATEGIES`, strict strategy tests |
| 10 | Legacy renderer mismatch | V3 blueprint is persisted and passed as a first-class renderer contract | `v3_pipeline.py`, `v3_directives` |
| 11 | Template cycling | V3 explicitly disables legacy template cycling | `disable_templates=True`, `composer._apply_templates` |
| 12 | Generic overlays | Overlays are purpose-specific, bounded, deduplicated and tied to the core/payoff language | `v3_engine.py` |
| 13 | Script before footage | Script is mapped onto immutable V3 beats and scene selection happens against those beats | `edit_planner._fit_script_to_beats` |
| 14 | Primitive scene matching | Scene selection combines lexical relevance, salience, role and optional semantic matching module | `edit_planner.choose_scene`, `v3_semantics` |
| 15 | 30% coverage gate | CI coverage floor is 50% | `.github/workflows/python-tests.yml` |
| 16 | Weak strategy tests | Tests cover all supported strategies and direct render mappings | `test_v3_strict_hardening.py`, `test_v3_effect_mapping.py` |
| 17 | Weak adversarial tests | Invalid config, NaN/Inf, semantic fallback, FFmpeg duration and render contracts are tested | `test_v3_strict_hardening.py` |
| 18 | No scene rejection | V3 scene matcher has a minimum score threshold and fails closed when no candidate qualifies | `edit_planner.choose_scene` |
| 19 | QC bypass | V3 refuses `skip_qc` unless an explicit development environment override exists | `v3_pipeline.py` |
| 20 | PR drift | Hardening branch is based directly on current `main`; PR #6 targets current main | PR #6 |
| 21 | V2 debt | Remaining compatibility modules are isolated and V3 path is explicit | V3 wrapper/modules |
| 22 | Maintainability | New V3 enforcement code is isolated into focused modules with explicit docstrings/contracts | `v3_engine`, `v3_quality`, `v3_semantics`, `v3_capabilities` |
| 23 | Fake capability count | 40 capabilities have structured implementation, validator, tests and status metadata | `v3_capabilities.py` + registry test |
| 24 | Real video quality unproven | CI validates media invariants; final artistic acceptance remains an explicit real-render gate | `v3_render_qc.json`, PR boundary |

## Release rule

The V3 release path must pass repository CI, strict render QC, dependency audit, Windows smoke tests and Docker smoke tests. No green unit test is treated as proof of artistic quality; the real rendered video remains subject to human acceptance.
