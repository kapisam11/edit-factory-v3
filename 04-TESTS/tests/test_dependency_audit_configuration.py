"""Regression tests for dependency audit and patched lockfile policy."""
from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.version import Version


ROOT = Path(__file__).resolve().parents[2]


def test_project_requires_a_patched_filelock_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = project["project"]["dependencies"]
    assert any(requirement == "filelock>=4.2.4,<5" for requirement in requirements)


def test_universal_lockfile_resolves_patched_filelock() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    matches = [package for package in lock["package"] if package["name"] == "filelock"]

    assert len(matches) == 1
    assert Version(matches[0]["version"]) >= Version("4.2.4")


def test_both_pip_audit_workflows_skip_the_editable_project() -> None:
    workflows = (
        ROOT / ".github" / "workflows" / "python-tests.yml",
        ROOT / ".github" / "workflows" / "security.yml",
    )
    for workflow in workflows:
        content = workflow.read_text(encoding="utf-8")
        assert "run: uv run pip-audit --skip-editable" in content, workflow.name
