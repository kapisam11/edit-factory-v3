# V3 hostile-review acceptance matrix

This document defines release-gating acceptance criteria for the 24 findings from the hostile engineering review.

| # | Finding | Release acceptance condition | Evidence |
|---:|---|---|---|
| 1 | Blueprint/render mismatch | V3 timeline boundaries and count exactly match the immutable blueprint | `edit_planner.build_timeline`, `v3_pipeline._validate_timeline_contract` |
| 2 | Exact duration | Short sources are padded; final media is normalized and probed against target duration | `render_engine.render_segment`, `v3_quality.normalize_duration` |
| 3 | Retention only advisory | V3 renders and persists a no-retention baseline, applies retention effects afterward, and verifies final-vs-baseline local pixel deltas before release QC passes | `v3_pipeline.run_v3_pipeline`, `v3_quality.verify_retention_against_baseline` |
| 4 | Directive/effect seam | V3 motion/transition directives resolve to valid FFmpeg video filters | `effects_engine`, `test_v3_effect_mapping.py` |
| 5 | QC naming | Planning/editorial QC and post-render media QC are separate machine-readable gates | `v3_engine`, `v3_quality` |
| 6 | Fake predictive metrics | Retention/completion/rewatch/share values are explicitly heuristic, never platform predictions | `00-INFO/V3-METRICS.md` |
| 7 | Shallow emotion detection | Optional semantic encoder is combined with deterministic lexical fallback | `v3_semantics.py` |
| 8 | Weak audience awareness | Audience is an explicit V3 input carried into planning and script-generation context | `V3Config`, `v3_pipeline._research_summary_from_blueprint` |
| 9 | Cyclic edit strategy | Edit decisions are selected from shot characteristics rather than relying on index cycling alone | `edit_planner._adaptive_motion_transition` |
| 10 | Legacy renderer mismatch | V3 persists a first-class blueprint contract and validates the produced timeline against it | `v3_pipeline.py` |
| 11 | Template cycling | V3 disables the legacy template layer explicitly | `composer._apply_templates`, `disable_templates=True` |
| 12 | Generic overlays | Overlay generation is bounded, purpose-specific and deduplicated | `v3_engine._overlay_for_purpose`, `_unique_overlay` |
| 13 | Script before footage | V3 performs source-footage analysis before the production script call and then maps script onto immutable beats | `v3_pipeline._build_footage_evidence`, `edit_planner._fit_script_to_beats` |
| 14 | Primitive scene matching | Scene choice combines lexical and semantic relevance with salience/role signals and fails closed below confidence threshold | `edit_planner.choose_scene` |
| 15 | Coverage gate | Maintained V3 production surface has a CI coverage floor of at least 80% | `.github/workflows/python-tests.yml` |
| 16 | Weak strategy tests | All edit types and direct renderer mappings are exercised | `test_v3_strict_hardening.py`, `test_v3_effect_mapping.py` |
| 17 | Weak adversarial tests | Invalid configuration, fail-closed scene matching, media normalization and render contracts are exercised | `test_v3_strict_hardening.py` |
| 18 | No scene rejection | Independent relevance confidence is required before salience/role bonuses can select a scene | `edit_planner.choose_scene`, `min_scene_match_score=0.15` |
| 19 | QC bypass | V3 rejects `skip_qc` unless the explicit development override is present | `v3_pipeline.py` |
| 20 | PR drift | Production-hardening PR must target `main`, stay mergeable, and pass the release CI suite before release | current hardening PR |
| 21 | V2 debt | V3 is isolated behind an explicit wrapper/contract and no legacy template path is allowed to silently override V3 | `v3_pipeline.py`, `composer._apply_templates` |
| 22 | Maintainability | V3 enforcement responsibilities are separated into focused modules with typed contracts | `v3_engine`, `v3_quality`, `v3_semantics`, `v3_capabilities`, `edit_planner` |
| 23 | Capability truthfulness | Exactly 40 registry entries remain compatible, but each entry exposes evidence level, status, display name, and truth note so heuristics are not represented as equivalent to human review | `v3_capabilities.validate_capabilities`, truthfulness tests |
| 24 | Real video quality unproven | CI performs a real dashboard-driven V3 render, proves media invariants, independent retention QC, semantic/dead-moment heuristics, and V3 readiness evidence; a human visual review remains the final artistic gate | `.github/workflows/e2e.yml`, V3 QC/readiness artifacts, release rule |

## Release rule

Release is blocked until repository CI is green, including the Python matrix, Windows smoke, dependency audit, Docker smoke, and a real dashboard-driven V3 render. Production also requires V3 timeline/render contracts, runtime capability checks, and secure deployment configuration. Static verification does not claim aesthetic excellence; a real rendered video still requires human visual acceptance.
