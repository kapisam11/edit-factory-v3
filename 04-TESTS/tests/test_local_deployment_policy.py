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


def test_dev_memory_admission_is_below_default_container_limit() -> None:
    compose = (DEPLOYMENT_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert 'mem_limit: "${AIVF_CONTAINER_MEMORY:-8g}"' in compose
    assert "AIVF_MAX_JOB_MEMORY_MB: ${AIVF_MAX_JOB_MEMORY_MB:-6144}" in compose
    assert "AIVF_MAX_RESERVED_MEMORY_MB: ${AIVF_MAX_RESERVED_MEMORY_MB:-7168}" in compose


def test_windows_launcher_uses_buildable_development_compose() -> None:
    launcher = (REPOSITORY_ROOT / "START-ALL.ps1").read_text(encoding="utf-8")
    compose_assignment = next(
        line for line in launcher.splitlines() if line.startswith("$ComposeFile =")
    )

    assert "docker-compose.dev.yml" in compose_assignment
    assert "docker-compose.yml\\" not in compose_assignment
    assert '$composeUpArgs += "--build"' in launcher
    assert '$BaseUrl = "http://127.0.0.1:$DashboardPortNumber"' in launcher
    assert "AIVF_DASHBOARD_PORT must be a TCP port number" in launcher
    assert launcher.rstrip().endswith("exit 0")
    assert sum(line.strip() == "exit 0" for line in launcher.splitlines()) == 2


def test_documentation_distinguishes_profiles_from_direct_publishers() -> None:
    upload_guide = (
        REPOSITORY_ROOT / "00-INFO" / "UPLOAD-PACKAGE.md"
    ).read_text(encoding="utf-8")
    autonomous_guide = (
        REPOSITORY_ROOT / "00-INFO" / "AUTONOMOUS-YOUTUBE.md"
    ).read_text(encoding="utf-8")

    assert "not proof that your own accounts can publish" in upload_guide
    assert "controlled real-account acceptance test" in upload_guide
    assert "does not watch a folder of existing videos" in upload_guide
    assert "publishes through the YouTube integration only" in autonomous_guide


def test_dev_compose_does_not_supply_known_development_secret_defaults() -> None:
    compose = (DEPLOYMENT_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")
    assert "FLASK_SECRET_KEY: ${FLASK_SECRET_KEY:-}" in compose
    assert "AIVF_DASHBOARD_TOKEN: ${AIVF_DASHBOARD_TOKEN:-}" in compose
    assert "dev-secret-change-me" not in compose
    assert "dev-token-change-me" not in compose


def test_wsgi_runtime_rejects_missing_or_placeholder_secrets_outside_tests(monkeypatch) -> None:
    from dashboard_auth import validate_dashboard_secrets

    monkeypatch.setenv("AIVF_ENV", "development")
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv("AIVF_DASHBOARD_TOKEN", "a-real-looking-token")
    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY is required"):
        validate_dashboard_secrets()

    monkeypatch.setenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    with pytest.raises(RuntimeError, match="known development placeholder"):
        validate_dashboard_secrets()

    monkeypatch.setenv("FLASK_SECRET_KEY", "a-generated-local-secret")
    monkeypatch.setenv("AIVF_DASHBOARD_TOKEN", "dev-token-change-me")
    with pytest.raises(RuntimeError, match="known development placeholder"):
        validate_dashboard_secrets()

    monkeypatch.setenv("AIVF_ENV", "test")
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.delenv("AIVF_DASHBOARD_TOKEN", raising=False)
    validate_dashboard_secrets()
