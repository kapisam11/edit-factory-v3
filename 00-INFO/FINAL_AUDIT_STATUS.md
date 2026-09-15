# Final Audit Status

This document records the current evidence state of Edit Factory v3. It intentionally does not certify the repository as 10/10 from source inspection alone.

## Verified hardening in the current branch

- V3 keeps an explicit blueprint/timeline contract and post-render QC path.
- FFmpeg/FFprobe invocation in the hardened media boundary is argv-based, uses resolved executables, and has bounded subprocess timeouts.
- Rendered media is probed for a valid video stream, positive duration, and valid dimensions.
- V3 no longer monkey-patches a module-global production renderer callback; legacy auto-fix is disabled through an explicit pipeline argument.
- V3 input validation rejects missing/empty source media and bounds topic, audience, platform, BPM, and target-duration inputs.
- V3 blueprint/QC and production JSON/text artifacts use atomic temp-file replacement in the hardened paths.
- Subtitle drawtext text escapes FFmpeg filter metacharacters and subtitle probing fails closed instead of inventing a duration.
- Real render integration tests and adversarial V3 contract tests exist, with the V3 coverage gate raised to 60% in CI.
- Python support is declared as 3.10+ and CI targets Python 3.10–3.13.
- Docker runs the production WSGI service as the dedicated non-root `aivf` user.

## Current limitations that block release certification

- There is currently no committed dependency lockfile. The historical `uv.lock` was empty and was removed rather than treating an empty file as reproducibility evidence. CI therefore resolves dependencies during each run.
- A fresh pull-request CI run for the current hardening branch has not yet been completed; the previous V3 hardening PR was already merged before this remediation pass.
- Repository-only inspection cannot certify artistic quality. A real rendered video still requires human visual acceptance.
- Target-host cancellation, restart/reconciliation, persistent-volume, and production-secret checks remain deployment acceptance gates.

## Historical notes

Some older documents describe Python 3.9 support or frozen dependency environments. Those statements are historical and must not be treated as current release requirements until they are explicitly updated.

## Release rule

Do not label Edit Factory V3 "10/10 verified" until the automated CI gates are green on the current branch and the deployment/real-render acceptance gates have actually passed.
