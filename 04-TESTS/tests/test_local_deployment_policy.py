"""Regression checks for the single-PC deployment and publishing scope."""
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_ROOT = REPOSITORY_ROOT / "06-CONFIG-AND-DEPLOYMENT"


@pytest.mark.parametrize(
    "compose_name",
    ["docker-compose.yml", "docker-compose.dev.yml"],
)
def test_dashboard_port_is_loopback_bound_by_default(compose_name: str) -> None:
    compose = (DEPLOYMENT_ROOT / compose_name).read_text(encoding="utf-8")

    assert '"127.0.0.1:${AIVF_DASHBOARD_PORT:-5000}:5000"' in compose
    assert '- "5000:5000"' not in compose


@pytest.mark.parametrize(
    "compose_name",
    ["docker-compose.yml", "docker-compose.dev.yml"],
)
def test_default_job_concurrency_is_one(compose_name: str) -> None:
    compose = (DEPLOYMENT_ROOT / compose_name).read_text(encoding="utf-8")

    assert "AIVF_MAX_CONCURRENT_JOBS: ${AIVF_MAX_CONCURRENT_JOBS:-1}" in compose


def test_windows_launcher_uses_buildable_development_compose() -> None:
    launcher = (REPOSITORY_ROOT / "START-ALL.ps1").read_text(encoding="utf-8")
    compose_assignment = next(
        line for line in launcher.splitlines() if line.startswith("$ComposeFile =")
    )

    assert "docker-compose.dev.yml" in compose_assignment
    assert "docker-compose.yml\\" not in compose_assignment
    assert '$composeUpArgs += "--build"' in launcher


def test_documentation_distinguishes_profiles_from_direct_publishers() -> None:
    upload_guide = (
        REPOSITORY_ROOT / "00-INFO" / "UPLOAD-PACKAGE.md"
    ).read_text(encoding="utf-8")
    autonomous_guide = (
        REPOSITORY_ROOT / "00-INFO" / "AUTONOMOUS-YOUTUBE.md"
    ).read_text(encoding="utf-8")

    assert "not a promise that each platform can be uploaded to automatically" in upload_guide
    assert "does not watch a folder of existing videos" in upload_guide
    assert "publishes through the YouTube integration only" in autonomous_guide
