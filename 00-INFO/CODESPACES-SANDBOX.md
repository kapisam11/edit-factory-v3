# Edit Factory v3 — Codespaces sandbox

This is a **development and verification environment**, not a production deployment. Use it to make changes on a branch, exercise the media toolchain in a clean Linux container, and review CI evidence before merging.

## Open the sandbox

1. Open the repository on GitHub and select the branch `sandbox/codespaces-media-verification-2026-10-13` (or your own feature branch).
2. Select **Code → Codespaces → Create codespace on this branch**.
3. Let the dev container build. It installs FFmpeg, FFprobe, Tesseract, Python 3.12, and the locked `web`, `dev`, and `ocr` dependencies.
4. Keep experiments and commits on the feature branch. Do not use `main` as the work branch.

The environment is deliberately CPU/Linux based. It does not prove Windows-specific behavior, GPU acceleration, or performance on the production PC.

## Verify a real media toolchain

From the repository root, run:

```bash
bash .devcontainer/verify-media.sh
```

The script generates a temporary three-second H.264/AAC video using FFmpeg, checks its stream metadata and duration with FFprobe, then runs the existing media-health, render-integration, render-engine, golden-media, and output-QC tests. The generated fixture is deleted on exit.

Run the full Python suite when a change touches shared behavior:

```bash
uv run pytest -q 04-TESTS/tests
```

Run lint, types, and diagnostics as needed:

```bash
uv run ruff check 01-MAIN-CODE 02-WEB-FILES 03-SIDE-CODE --select E9,F --ignore F401,F811,F841
uv run mypy
uv run aivf-ready .
uv run python -m ai_video_factory.security_audit .
```

For dashboard/render end-to-end testing, use the existing **Dashboard E2E** workflow. It creates synthetic source media, exercises the authenticated dashboard V3 render path, and checks the resulting output package and media evidence. The Python-package workflow also runs the media/render test groups. GitHub Actions is the repeatable clean-runner check; local success alone is not a merge signal.

## Safe change and pull-request flow

1. Create a focused branch from current `main`.
2. Make one logically related change and add regression tests.
3. Run the relevant sandbox tests.
4. Push the branch and open a pull request against `main`.
5. Review the diff and wait for applicable Python, Docker, security, dashboard E2E, and V3 editorial-regression checks.
6. Fix failures and rerun checks before merging. Do not merge based only on a successful container build.

The sandbox contains no publishing credentials by default. Do not add real platform tokens, OAuth files, private source media, or production state to the repository or CI logs.

## Enforcement note

A pull request creates a review point, but it does not by itself prevent a direct push to `main`. If repository administration is available, configure a branch ruleset for `main` that requires pull requests and the applicable CI checks, and disallows bypass where appropriate. Verify the required-check names against the checks GitHub reports for the branch.
