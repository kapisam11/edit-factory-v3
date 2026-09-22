# Final Audit Status

This document records the current evidence state of Edit Factory v3. It intentionally does not certify the repository as 10/10 from source inspection alone.

## Verified hardening in the current branch

- V3 keeps an explicit blueprint/timeline contract and post-render QC path.
- FFmpeg/FFprobe invocation in the hardened media boundary is argv-based, uses resolved executables, and has bounded subprocess timeouts.
- Rendered media is probed for a valid video stream, positive duration, and valid dimensions.
- V3 no longer monkey-patches a module-global production renderer callback; legacy auto-fix is disabled through an explicit pipeline argument.
- V3 input validation rejects missing/empty source media and bounds topic, audience, platform, BPM, and target-duration inputs against the blueprint limits.
- V3 blueprint/QC and production JSON/text artifacts use atomic temp-file replacement in the hardened paths.
- Subtitle drawtext text escapes FFmpeg filter metacharacters and subtitle probing fails closed instead of inventing a duration.
- Real render integration tests, adversarial V3 contract tests, and a real dashboard-driven V3 E2E render exist. The maintained V3 coverage gate is 80%.
- Python support is 3.10+ and the locked CI matrix exercises Python 3.10–3.12.
- A committed `uv.lock` is present and both CI and the production Docker image install from it with `uv sync --frozen`.
- Model-provider failures use bounded retries for transient HTTP/transport failures and degrade to deterministic planning when the provider remains unavailable.
- Docker runs the production WSGI service as the dedicated non-root `aivf` user.

## Current engineering status

The repository-level findings from the September 21 hostile review are closed in the current hardening line:

- Scene relevance is enforced per candidate before ranking.
- V3 preview resolution is canonical-only and FFprobe-validates the artifact before serving it.
- OCR E2E fails closed and asserts OCR-derived evidence reaches the scene index.
- The deployment README newline defect is fixed.
- Gunicorn uses a finite request timeout aligned with the SSE lifetime cap.
- Detailed health diagnostics are authenticated; the public health contract is minimal.
- Total-storage filesystem scans are reconciliation work, throttled separately from the per-job enforcement loop.
- V3 rendering crosses an explicit V3RenderRequest compatibility adapter that forces legacy auto-fix and legacy package finalization off; a regression test locks that contract.
- Retry credentials are never persisted. If process-local retry credentials have expired or the process restarted, the authenticated retry endpoint now accepts the required credentials explicitly for that retry and retains them only in bounded process memory.

## Additional hardening applied after the 2026-09-21 review

- V3 voiceover generation/mixing now fails the V3 production contract instead of silently degrading when voiceover is requested.
- Silent-source voiceover mixing preserves the source video duration by padding/trimming the voiceover explicitly.
- Required V3 artifact-readiness failures now propagate into the job result.
- Face analysis is capped to a bounded number of source samples and adapts the sampling interval for long source videos.
- Login-attempt tracking prunes stale client keys once the in-memory set grows beyond a bounded threshold.
- CI pins GitHub Actions to immutable commit SHAs, runs staged mypy checks, and measures dashboard/resource-governance modules in the production coverage gate.
- The production Docker image uses a pinned Python base-image digest and excludes test/extension trees from the runtime image.
- Deployment includes an automated target-host V3 production smoke covering authenticated job creation, real V3 rendering, readiness, FFprobe validation, and preview.

## Remaining external acceptance gates

These are deployment evidence gates, not unresolved repository defects:

- Run the exact release artifact on the target production host.
- Verify cancellation/restart reconciliation and no orphan FFmpeg processes.
- Verify persistent-volume behavior and retention/cleanup on the real host.
- Verify TLS/reverse-proxy configuration and production cookie settings.
- Verify production secrets are injected through the deployment environment and are not written to job state.
- Render a real V3 production input and perform human visual/audio acceptance of the resulting media.

## Historical notes

The earlier branch advertised Python 3.9 support, but the frozen dependency environment does not install successfully on Python 3.9. The package metadata, CI matrix, and operational documentation now consistently declare the verified Python 3.10–3.12 support floor.

## Release rule

Do not label Edit Factory V3 "10/10 verified" until the automated CI gates are green on the current branch and the deployment/real-render acceptance gates have actually passed.
