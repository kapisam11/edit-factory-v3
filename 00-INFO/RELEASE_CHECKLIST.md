# Production Release Checklist

## Automated release gates

Before merging a production/hardening branch, all of the following automated checks must be green:

- Python 3.9–3.12 CI test matrix
- repository-wide Ruff checks for production Python
- `python 01-MAIN-CODE/cli.py --help`, `python 01-MAIN-CODE/cli_v3.py --help`, `aivf --help`, and `aivf-v3 --help`
- dependency audit against the committed `uv.lock`
- locked-environment verification with `uv sync --frozen`
- Docker build and dashboard health/import smoke test
- dashboard authentication test
- secret persistence/API redaction tests
- malicious upload/path tests
- real FFmpeg render integration test
- SQLite concurrent-write test
- final documentation review against implemented code

The repository contains a committed dependency lockfile, and the release CI consumes it with `--frozen`. Dependency changes must update `uv.lock` in the same change set.

## Deployment acceptance gates

These require the actual deployment target and cannot be certified by source inspection alone:

- end-to-end cancellation test on the deployment operating system
- restart/reconciliation test on the deployment operating system
- one real end-to-end render using the target host's FFmpeg/FFprobe installation
- verification of persistent `/app/state` storage (or the equivalent configured state directory)
- verification that production secrets are supplied through the deployment environment
- human visual acceptance of the rendered result for framing, readability, pacing, synchronization, and artistic quality

Do not label a deployment "10/10 verified" until both the automated release gates and the target-host acceptance gates have actually passed. Source changes alone are not evidence that the runtime is healthy.
