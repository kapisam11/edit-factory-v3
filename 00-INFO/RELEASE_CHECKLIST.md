# Production Release Checklist

## Automated release gates

Before merging a production/hardening branch, all of the following automated checks must be green:

- Python 3.9–3.12 CI test matrix
- repository-wide Ruff checks for production Python
- `python cli.py --help`, `python cli_v2.py --help`, and `aivf --help`
- dependency audit
- Docker build and dashboard import smoke test
- dashboard authentication test
- secret persistence/API redaction tests
- malicious upload/path tests
- real FFmpeg render integration test
- SQLite concurrent-write test
- final documentation review against implemented code

## Deployment acceptance gates

These require the actual deployment target and cannot be certified by source inspection alone:

- end-to-end cancellation test on the deployment operating system
- restart/reconciliation test on the deployment operating system
- one real end-to-end render using the target host's FFmpeg/FFprobe installation
- verification of persistent `/app/state` storage (or the equivalent configured state directory)
- verification that production secrets are supplied through the deployment environment

Do not label a deployment "10/10 verified" until both the automated release gates and the target-host
acceptance gates have actually passed. Source changes alone are not evidence that the runtime is healthy.
