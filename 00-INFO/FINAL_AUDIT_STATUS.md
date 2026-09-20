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

## Current limitations that block release certification

- The fresh pull-request CI run must finish green on the latest hardening head before automated verification can be certified.
- Repository-only inspection cannot certify artistic quality. A human still needs to visually accept a real rendered V3 output.
- Target-host cancellation, restart/reconciliation, persistent-volume, TLS/reverse-proxy, and production-secret checks remain deployment acceptance gates.

## Historical notes

The earlier branch advertised Python 3.9 support, but the frozen dependency environment does not install successfully on Python 3.9. The package metadata, CI matrix, and operational documentation now consistently declare the verified Python 3.10–3.12 support floor.

## Release rule

Do not label Edit Factory V3 "10/10 verified" until the automated CI gates are green on the current branch and the deployment/real-render acceptance gates have actually passed.
