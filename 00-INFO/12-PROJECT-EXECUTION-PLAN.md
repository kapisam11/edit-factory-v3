# Edit Factory V3 — Actionable Project Execution Plan

**Status:** Proposed execution baseline  
**Baseline reviewed:** 10 October 2026  
**Planning horizon:** 11 weeks from an approved kickoff date; relative weeks are estimates, not calendar commitments  
**Decision rule:** Existing controls must be verified before new ones are built. A documented feature is not accepted as working until its tests or operational evidence support that claim.

## 1. Purpose and scope

This plan turns the general project-planning recommendations into an execution sequence for the existing Edit Factory V3 repository. It covers product changes, integration, verification, release readiness, and operational handover. It is not a commitment to rewrite the architecture or to add infrastructure without evidence.

The current architecture is intentionally single-host: Flask dashboard, SQLite state, spawned job workers, and FFmpeg/FFprobe. Keep that design unless measured load, availability requirements, or operational evidence justify a change.

### Outcomes

1. A coherent, conflict-free video-thinking and creator-learning implementation, integrated with the current main branch.
2. Bounded and observable media jobs with validated inputs and outputs; reuse existing admission/resource controls where they meet the requirements.
3. Learning results backed by real annotations and held-out evaluation, with uncertainty routed to review rather than represented as fact.
4. Repeatable deployment and recovery, with evidence-based release gates.
5. A clear operator/tester handover, including limitations, known issues, and rollback instructions.

### Out of scope unless separately approved

- Distributed queues, microservices, Kubernetes, or a multi-host rewrite.
- Claims that the system has learned reliable human taste without sufficient labelled data and holdout results.
- Autonomous publication when policy, rights, factuality, artifact quality, or model confidence checks require review.
- Fixed calendar dates or staffing commitments before owners and kickoff are confirmed.

## 2. Work breakdown, deliverables, and acceptance gates

| ID | Work package | Main activities | Deliverable | Acceptance gate | Estimate |
|---|---|---|---|---|---|
| WP0 | Baseline and requirements | Capture current main SHA, open PRs, CI/security/E2E status, known defects, supported runtime and deployment assumptions. Confirm priorities with the project owner. | Baseline report, scoped backlog, reproducible test instructions | Every requirement has an owner, priority, test/evidence method, and an explicit in/out-of-scope decision. | Weeks 1–2 |
| WP1 | Feature branch integration | Compare overlapping video-thinking/learning branches; identify unique changes; use one current-main-based integration line; resolve conflicts semantically, not by blindly accepting one side. | One reviewed integration PR and a change map | No unresolved conflicts; no duplicated feature stage; targeted tests and full required CI pass; no accidental loss of existing publishing/analytics behavior. | Weeks 1–3, overlaps WP0 |
| WP2 | Runtime and media safety | Verify upload signature/probe checks, duration/resolution/frame-rate/codec/audio constraints, disk and time admission, worker attempt isolation, atomic publication, and post-render validation. Fix only evidence-backed gaps. | Safety-gap matrix, code/tests for confirmed gaps | Oversized/invalid inputs are rejected before expensive work; outputs are validated before being exposed; concurrent jobs respect configured capacity and disk budget; stale workers cannot publish over a newer attempt. | Weeks 3–6 |
| WP3 | Learning and editorial evaluation | Verify annotation provenance, deduplication by source video, missing-value handling, baseline-vs-model holdout metrics, cold-start behavior, and review routing. Add representative tests/data where needed without fabricating labels. | Evaluation report and reproducible learning workflow | No invented performance labels; data leakage is prevented by the holdout split; model must beat the documented simple baseline before it is described as useful for personalization; otherwise retain baseline behavior and report inconclusive results. | Weeks 4–7 |
| WP4 | Reliability and release operations | Exercise strict release checks, model locks/hashes, migrations, off-host backup verification, restore drills, retention behavior, digest-pinned deployment and rollback. | Release evidence bundle and recovery runbook | Required CI/security/E2E and target smoke checks are green; pinned image and model digests verify; a backup restore succeeds in isolation; rollback is rehearsed or the release is blocked. | Weeks 6–9 |
| WP5 | Acceptance and handover | Run end-to-end user scenarios on a clean environment; collect feedback; document known issues, operator actions, rollback, and support ownership. | UAT record, release notes, known-issues list, handover checklist | All critical scenarios pass or are explicitly blocked from release; no unresolved critical/high-severity issue without written risk acceptance; owner approves release. | Weeks 10–11 |

**Overlap is intentional:** WP1 begins after the baseline inventory starts; testing and documentation run throughout. WP4 should not be postponed until the end because backup, migration, and rollback failures are expensive to discover late.

## 3. Priority and responsibility model

Use role names until real people are assigned. One person may fill several roles in a small project, but the responsibilities should still be explicitly covered.

| Role | Responsibility | Required decision/evidence |
|---|---|---|
| Project owner / release approver | Scope, priority, risk acceptance, release sign-off | Approves scope changes and the final release record |
| Technical lead / integrator | Architecture, PR consolidation, conflict resolution, migration compatibility | Reviews integration diff and records decisions |
| Feature engineer | Video thinking, creator feedback, publishing integration, backward compatibility | Targeted tests and implementation notes |
| Reliability / security owner | Media admission, secrets, model integrity, backup/restore, deployment, rollback | Security and operational evidence |
| QA / editorial evaluator | Regression suite, UAT scenarios, test corpus, model holdout evaluation | Test report with reproducible failures and pass criteria |
| Operator / user representative | Real workflow feedback and production readiness | Confirms operating instructions and user-facing behavior |

### Working rules

- Every work item must identify: owner, priority, dependency, implementation location, test command or evidence, and acceptance criteria.
- Changes to media execution, publishing, job ownership, storage schema, or release behavior require regression tests and documentation updates.
- Do not label a task “done” because code was committed. It is done only when its acceptance evidence is recorded.
- Do not merge overlapping feature PRs just to clear the queue. Inventory both diffs, preserve unique changes, choose one coherent integration, and close/supersede alternatives only after their unique work is accounted for.
- Keep a decision log for changes that affect behavior, schema, deployment, rights/policy, or learning methodology.

## 4. Relative timeline and milestones

The original generic estimate is retained as an 11-week planning envelope, adjusted for an already-existing codebase. Dates should be filled in only after the project owner approves kickoff and capacity.

```text
Relative week       1  2  3  4  5  6  7  8  9 10 11
WP0 Baseline        █  █
WP1 Integration     █  █  █
WP2 Media safety          █  █  █  █
WP3 Learning                 █  █  █  █
WP4 Release ops                     █  █  █  █
WP5 Acceptance                                  █  █
```

| Milestone | Target | Exit criteria |
|---|---|---|
| M1 — Baseline agreed | End of week 2 | Scope and risks reviewed; CI/PR/production baseline captured; owners assigned |
| M2 — Feature integration stable | End of week 3 | One coherent feature line, conflict-free diff, targeted checks passed |
| M3 — Runtime and learning gates met | End of week 7 | Media/resource and evaluation criteria have reproducible evidence; gaps are fixed or explicitly release-blocking |
| M4 — Recovery and release evidence complete | End of week 9 | Strict release gate inputs, digest verification, backup restore, and rollback evidence available |
| M5 — Acceptance / handover | End of week 11 | Critical UAT flows pass; known issues and runbook accepted; release approver signs off |

**Schedule changes:** Re-estimate when a milestone misses its exit criteria, a high-severity defect is found, requirements change, or a required external credential/environment is unavailable. Do not shorten validation by silently dropping required checks.

## 5. Definition of done and verification

### Change-level definition of done

- The change has a clear user-visible or operational purpose.
- Tests cover the changed behavior and relevant failure modes.
- Existing behavior and public interfaces remain compatible, or a migration/deprecation path is documented.
- Static checks, targeted tests, and required full CI checks pass.
- Security-sensitive data, credentials, real user/channel analytics, and generated media are not fabricated or committed.
- Runbooks and repository maps are updated when workflows or architecture change.
- The PR describes behavior, risks, configuration changes, evidence, and rollback considerations.

### Local verification baseline

Use the project’s existing developer instructions and run the applicable checks from the repository root:

```bash
python -m pip install -e '.[web,dev]'
pytest -q
ruff check .
```

For production/release work, also run and record the repository’s applicable diagnostics, E2E, media integration, packaging, security, model-lock, migration, and release-gate workflows. Do not treat local unit tests as a substitute for real target-host smoke tests or backup restoration.

### User acceptance scenarios

1. **Blueprint:** Generate a V3 blueprint offline; validate its schema and ensure pre-render technical validity is not claimed before a real artifact exists.
2. **Media production:** Use a small, licensed test clip; reject malformed or over-limit input; complete a render; verify output metadata and playable video/audio.
3. **Dashboard lifecycle:** Create a job, observe progress/logs, preview a completed video, and retry a failed/interrupted job where supported.
4. **Publishing safety:** Missing/uncertain rights, factuality, disclosure, or thinking evidence must follow the documented review policy and must not silently bypass safeguards.
5. **Learning:** Add real human feedback, deduplicate imported video metrics, run held-out evaluation, and confirm that missing metrics remain missing rather than becoming fabricated zeros.
6. **Recovery:** Verify a backup, restore it into an isolated destination, validate the restored database, and rehearse a release rollback without overwriting the only working deployment.

Record environment, commit SHA, command/scenario, result, timestamp, and failure evidence for every release-critical check.

## 6. Risk register

Probability/impact are initial planning assessments and must be revisited in WP0.

| ID | Risk | Initial level | Mitigation / trigger | Owner |
|---|---|---|---|---|
| R1 | Overlapping video-thinking PRs lose unique code or duplicate behavior | High | Map both diffs to requirements; integrate once on current main; regression-test analytics, rendering and publish guards. Trigger: overlapping edits or conflict. | Technical lead |
| R2 | Large or unusual media exhausts disk, CPU, memory, or worker time | High | Verify admission limits, disk reservations, job deadlines and concurrent capacity; load-test representative worst cases. Trigger: input limits or resource gauges exceeded. | Reliability owner |
| R3 | A stale worker overwrites a newer attempt’s output | High | Per-job/per-attempt isolated workspace, ownership token/fencing, validation and atomic final publish. Trigger: retry/cancel/recovery overlap. | Technical lead |
| R4 | Mutable/unverified model assets change results or execute unexpected bytes | High | Immutable asset source plus required SHA-256 lock verification; fail closed on mismatch. Trigger: model asset changes or absent lock. | Security owner |
| R5 | Learning is overfit, leaky, or based on insufficient real examples | High | Track provenance, separate train/holdout by video/time where possible, compare with simple baseline, disclose low sample size and uncertainty. Trigger: tiny/biased dataset or no baseline improvement. | Evaluation owner |
| R6 | Data loss or failed recovery after deployment | High | Encrypted off-host backups, manifest/hash validation, scheduled isolated restore tests and retention policy. Trigger: backup missing, stale, or restore fails. | Operator |
| R7 | Release labelled ready despite a failed or unverified gate | High | Strict release gate; capture CI, security, E2E, host smoke, approval, pinned image/model hashes and backup evidence. Missing evidence means blocked, not passed. | Release approver |
| R8 | Scope expands into premature distributed architecture | Medium | Keep single-host architecture while requirements and measurements support it; require a written ADR and load evidence for scaling changes. | Project owner |
| R9 | User acceptance discovers workflow regressions late | Medium | Run a small UAT loop during WP2–WP4; use licensed fixtures and real workflow scenarios. Trigger: major UI, CLI or publish-flow changes. | QA / user representative |
| R10 | Timeline depends on unavailable credentials, host access, or reviewers | Medium | Track external dependencies separately and use synthetic/local tests only for code paths that do not require real provider access. Never claim a real upload occurred without evidence. | Project owner |

Risk response options are: avoid, mitigate, transfer, accept, or escalate. Each high risk must have an owner and an explicit release-blocking condition or a written risk acceptance.

## 7. Change control and reporting

Use a lightweight weekly review. Report only evidence-backed statuses:

- **Green:** acceptance criteria met and evidence linked.
- **Amber:** work continues, but a dependency, unresolved question, or schedule risk exists.
- **Red / Blocked:** a required acceptance gate has failed or cannot be verified.

Every report should include completed deliverables, failed/pending checks, new risks, decisions needed, next milestone, and scope/schedule changes. Do not call the project “production-ready” solely because code exists or one workflow is green.

### Release decision

A release is permitted only when the strict release gate passes, the correct immutable image and model assets are verified, required security/E2E/host checks are green, backup/restore evidence is current, critical UAT scenarios pass, and the release approver signs off. If a check is unavailable, record the release as **blocked/unverified** rather than inventing a pass.

## 8. Assumptions to confirm at kickoff

- The project remains a single-host deployment unless evidence supports a change.
- The existing CI/test suite and release tooling are the verification baseline; this plan does not assume they all currently pass.
- Named people, budget, start date, deployment host, and service-level objectives have not been supplied; role owners and calendar dates therefore remain to be assigned.
- Actual YouTube metrics, creator feedback, credentials, model weights, and production behavior must come from their real sources. Examples and test fixtures must be clearly labelled.
- The schedule is an estimate, not a commitment. Re-plan from the baseline after the first two weeks.

## 9. Repository touchpoints

- Start-up and project map: `00-INFO/00-START-HERE.md`
- Operational architecture and release checklist: `00-INFO/11-ENGINEERING-AND-OPERATIONS.md`
- V3 contract: `00-INFO/V3-BLUEPRINT-CONTRACT.md`
- Strict release gate: `aivf-release-check` / `ai_video_factory.release_check`
- Runtime diagnostics: `aivf-diagnose`
- Database migration verification: `aivf-db-migrate`
- Backup/restore verification: `aivf-backup`
- Retention maintenance: `aivf-janitor`
- Learning evaluation: `aivf-learn evaluate` (where the learning CLI/feature is available on the integrated branch)

This plan coordinates existing mechanisms and defines their acceptance evidence; it does not claim they have all been executed successfully.
