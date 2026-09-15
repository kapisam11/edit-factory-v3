# Current V3 hardening status

This document records the state of the hostile-review remediation branch. It is intentionally not a claim of release approval.

- V3 hardening branch: `hardening/v3-true-10-10`
- The original V3 hardening PR was merged as commit `3d211fd74cfdce36fa0808fc646674b6073f2688`.
- Current remediation continues on the hardening branch after that merge.
- Python support is declared as 3.10+ because the V3 source uses Python 3.10 union-type syntax.
- A committed `uv.lock` is present and CI installs it with `uv sync --frozen` for reproducible dependency resolution.
- CI tests Python 3.10–3.13, Windows smoke, dependency audit, packaging/imports, and Docker smoke.
- FFmpeg/FFprobe subprocesses use argv-style execution and bounded timeouts in the hardened media modules.
- V3 no longer monkey-patches the production pipeline's module-global renderer callback; V3 disables legacy auto-fix through an explicit function argument.
- Media QC validates the presence of a video stream, positive duration/dimensions, target dimensions and duration, and observed visual changes near retention events.
- AI provider calls use bounded retries for transient failures and degrade to the deterministic planner instead of turning a temporary provider outage into a false production failure.

Release remains blocked until the fresh pull-request CI run is green and a real rendered video has passed the project's human visual acceptance gate.
