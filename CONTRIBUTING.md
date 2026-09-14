# Contributing to Edit Factory v2

Thanks for contributing to Edit Factory v2.

## Development setup

Use Python 3.9 or newer. From the repository root:

```bash
python -m venv .venv
python -m pip install -e '.[dev]'
```

Run the test suite:

```bash
pytest -q
```

Run linting and formatting checks:

```bash
ruff check .
```

## Pull requests

Keep changes focused and explain the user-visible or engineering impact in the PR description. Add or update tests for behavior changes and avoid committing generated media, local state, credentials, or machine-specific files.

For architecture-sensitive changes, update the relevant documentation under `00-INFO/` so the repository map and operational instructions stay accurate.

## Code quality

Prefer small, testable modules and explicit interfaces. Avoid introducing new compatibility wrappers when the underlying API can be updated directly. Keep secrets out of source control and do not persist runtime API keys in job records or generated artifacts.

## Commit messages

Use a short imperative subject, for example:

```text
fix: clean up runtime job state
refactor: simplify package discovery
```
