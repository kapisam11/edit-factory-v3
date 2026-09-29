# Production hardening audit — 2026-09-29

This branch records a second, repository-backed hardening pass against the current main tree. The audit deliberately distinguishes verified code from claims in older summaries.

## Verified baseline corrections

- The repository already had a root pyproject.toml and uv.lock; they were not reintroduced here.
- The release workflow already generated container SBOM/provenance and GitHub artifact attestations; this branch does not duplicate that infrastructure.
- The current scene-intelligence implementation is custom sampling/OpenCV-based analysis. PySceneDetect is not a current dependency and is not claimed as integrated.
- PR #15 had a verified subtitle-output cleanup issue in the no-subtitle copy path. The output is now removed when post-copy validation fails.
- The existing retry policy was extended rather than creating a second retry subsystem.

## Production guardrails added

- Safe path containment and filename sanitization.
- Atomic JSON/byte writes.
- Streaming SHA-256 fingerprints.
- Free-disk and executable preflight checks.
- Bounded, timeout-aware external tool execution.
- Media stream/duration/FPS contract validation.
- Black-frame, freeze-frame, silence and loudness analysis.
- Technical media quality scoring and fingerprints.
- Asset provenance and explicit rights-review states.
- Deterministic metadata guardrails.
- Strict AI response boundary helpers and heuristic prompt-injection rejection.
- Bounded retry classification, deterministic jitter, idempotency keys and stale-job detection.
- Environment diagnostics plus a packaged aivf-diagnose CLI.
- V3 pipeline artifacts for source media health, final media health, provenance, metadata guardrails and diagnostics.

## Important limitations

These guardrails are defensive, not guarantees. The AI prompt-injection detector is heuristic. Media quality scoring is technical and does not measure whether a video is entertaining. GPU-specific runtime performance still needs hardware-backed verification. The local execution environment used for this audit could not resolve github.com, so local test execution is not claimed; GitHub Actions is the verification path for this branch.

## Research applied

Current official guidance reviewed for this pass includes Python subprocess security, Python packaging, GitHub Actions security, GitHub artifact attestations, FFmpeg/ffprobe output inspection, and current PySceneDetect documentation. The adopted changes favor small local components over introducing distributed infrastructure without a demonstrated need.