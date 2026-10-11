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

    assert "not a promise that each platform can be uploaded to automatically" in upload_guide
    assert "does not itself watch a folder of existing videos" in upload_guide
    assert "publishes through the YouTube integration only" in autonomous_guide



def test_existing_file_publisher_has_durable_queue_and_explicit_windows_schedule() -> None:
    guide = (REPOSITORY_ROOT / "00-INFO" / "FOLDER-PUBLISHING.md").read_text(encoding="utf-8")
    publisher = (
        REPOSITORY_ROOT / "01-MAIN-CODE" / "ai_video_factory" / "folder_publisher.py"
    ).read_text(encoding="utf-8")
    project = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    runner = (REPOSITORY_ROOT / "RUN-DAILY-PUBLISH.ps1").read_text(encoding="utf-8")
    scheduler = (
        REPOSITORY_ROOT / "REGISTER-DAILY-PUBLISHING.ps1"
    ).read_text(encoding="utf-8")

    assert 'aivf-folder-publish = "ai_video_factory.folder_publisher:main"' in project
    assert "CREATE TABLE IF NOT EXISTS publishing_jobs" in publisher
    assert "UNIQUE(platform, account_id, source_sha256)" in publisher
    assert "BLOCKED_UNSUPPORTED" in publisher
    assert '"idempotency_directory": str(self.config.state_dir / f"{platform}-idempotency")' in publisher
    assert "ffprobe" in publisher
    assert "rights_cleared" in publisher
    assert "StartWhenAvailable" in scheduler
    assert "WakeToRun" in scheduler
    assert "aivf-folder-publish run-once" in runner
    assert "Instagram" in guide and "TikTok" in guide
    assert "real-account acceptance test" in guide
