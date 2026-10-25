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


def test_windows_launcher_fails_closed_without_uv_unless_checks_are_explicitly_skipped() -> None:
    launcher = (REPOSITORY_ROOT / "START-ALL.ps1").read_text(encoding="utf-8")
    uv_guard = (
        "if ($RunChecks -and -not $SkipChecks -and -not "
        "(Get-Command uv -ErrorAction SilentlyContinue))"
    )
    compose_start = 'Invoke-Native "docker" $composeUpArgs'

    assert uv_guard in launcher
    assert launcher.index(uv_guard) < launcher.index(compose_start)
    assert "uv is required to run startup checks" in launcher
    assert "or use -SkipChecks to explicitly skip code checks" in launcher
    assert "[switch]$RunChecks" in launcher
    assert "if (-not $RunChecks -or $SkipChecks)" in launcher
    assert "Run START-ALL.bat -RunChecks" in launcher
    assert "ps --services --status running" in launcher
    assert "Only stop the web service when Compose reports it as running" in launcher
    assert launcher.index("Checking for a previous dashboard instance") < launcher.index("Start-StartupSplash")
    assert "Get-Content -LiteralPath $welcomeSource -Encoding UTF8 -Raw" in launcher
    assert "Get-Content -LiteralPath $splashSource -Encoding UTF8 -Raw" in launcher
    assert "uv is not installed, so the full Python check suite was skipped." not in launcher


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
    assert launcher.count("function Set-EnvValue([string]$Name, [string]$Value)") == 1
    assert '$temporaryEnvFile = "$EnvFile.tmp"' in launcher
    assert "Move-Item -Path $temporaryEnvFile -Destination $EnvFile -Force" in launcher
    assert '"config",' in launcher and '"--quiet"' in launcher
    assert "Write-Host $token" not in launcher
    assert "Private admin-console unlock code (save it somewhere safe): $adminPanelCode" not in launcher


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
    assert "FLASK_SECRET_KEY: ${FLASK_SECRET_KEY:?Set a generated FLASK_SECRET_KEY in 06-CONFIG-AND-DEPLOYMENT/.env}" in compose
    assert "AIVF_DASHBOARD_TOKEN: ${AIVF_DASHBOARD_TOKEN:?Set a generated AIVF_DASHBOARD_TOKEN in 06-CONFIG-AND-DEPLOYMENT/.env}" in compose
    assert "dev-secret-change-me" not in compose
    assert "dev-token-change-me" not in compose


def test_wsgi_runtime_rejects_missing_or_placeholder_secrets_outside_tests(monkeypatch) -> None:
    from ai_video_factory.security_config import validate_dashboard_secrets

    monkeypatch.setenv("AIVF_ENV", "development")
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.setenv("AIVF_DASHBOARD_TOKEN", "a-real-looking-token")
    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY must be explicitly configured"):
        validate_dashboard_secrets()

    monkeypatch.setenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    with pytest.raises(RuntimeError, match="known development placeholder"):
        validate_dashboard_secrets()

    monkeypatch.setenv("FLASK_SECRET_KEY", "a-generated-local-session-secret-1234567890")
    monkeypatch.setenv("AIVF_DASHBOARD_TOKEN", "dev-token-change-me")
    with pytest.raises(RuntimeError, match="known development placeholder"):
        validate_dashboard_secrets()

    monkeypatch.setenv("AIVF_ENV", "test")
    monkeypatch.delenv("FLASK_SECRET_KEY", raising=False)
    monkeypatch.delenv("AIVF_DASHBOARD_TOKEN", raising=False)
    validate_dashboard_secrets()


def test_external_drive_backup_performs_archive_verification_and_restore_drill() -> None:
    script = (
        REPOSITORY_ROOT
        / "03-SIDE-CODE"
        / "scripts"
        / "windows"
        / "backup"
        / "BACKUP-EDIT-FACTORY.ps1"
    ).read_text(encoding="utf-8")
    assert "function Get-EditFactoryRepositoryRoot" in script
    assert "[System.IO.Path]::GetPathRoot" in script
    assert '"--entrypoint", "aivf-backup", "web"' in script
    assert '"backup",' in script
    assert '"verify",' in script
    assert '"restore",' in script
    assert "$RestoreCheckArgs" in script
    assert "restore drill passed" in script
    assert '"--uploads", "/app/uploads"' in script
    assert '"--state", "/app/state"' in script
    assert "BitLocker" in script
    assert "Remove-Item -Force" in script


def test_install_guide_picks_python_312_as_windows_production_baseline() -> None:
    guide = (REPOSITORY_ROOT / "00-INFO" / "01-INSTALL.md").read_text(encoding="utf-8")
    assert "Python 3.12" in guide
    assert "3.11–3.14" in guide


def test_hardware_runbook_keeps_concurrency_one_until_benchmarked() -> None:
    guide = (REPOSITORY_ROOT / "00-INFO" / "HARDWARE-BENCHMARK.md").read_text(encoding="utf-8")
    assert "60-second" in guide
    assert "AIVF_MAX_CONCURRENT_JOBS=1" in guide
    assert "Do not raise CPU limits" in guide


def test_readme_documents_small_group_windows_requirements_and_paid_work_checklist() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Windows small-group production requirements" in readme
    assert "Pre-paid-work checklist" in readme
    assert "real media smoke test" in readme
    assert "backup/restore verification drill" in readme
    assert "API failure and retry behavior" in readme
    assert "rights approval before publish" in readme
    assert "disk space headroom" in readme


def test_final_windows_checklist_documents_limits_recovery_and_publish_gates() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "Final Windows production checklist" in readme
    assert "AIVF_FOLDER_MAX_FILE_BYTES" in readme
    assert "AIVF_FOLDER_MAX_STAGED_BYTES" in readme
    assert "AIVF_FOLDER_MIN_FREE_DISK_BYTES" in readme
    assert "Crash/restart test" in readme
    assert "backup/restore verification drill" in readme
    assert "Missing or malformed rights evidence" in readme
    assert "private or unlisted account acceptance test" in readme


def test_security_workflow_has_pinned_secret_scanning() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "security.yml"
    ).read_text(encoding="utf-8")

    assert "Secret scanning (Gitleaks)" in workflow
    assert "gitleaks-action@e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e" in workflow
    assert "fetch-depth: 0" in workflow



def test_windows_ffmpeg_installer_persists_verified_path_between_ci_steps() -> None:
    installer = (DEPLOYMENT_ROOT / "install_ffmpeg_windows.ps1").read_text(encoding="utf-8")

    # The installer runs in its own PowerShell process; local PATH changes do not
    # survive into later GitHub Actions steps unless GITHUB_PATH is updated.
    assert "Test-FfmpegBinDirectory -Directory $ffmpegDirectory" in installer
    assert 'if ($env:GITHUB_PATH) {' in installer
    assert "Add-Content -LiteralPath $env:GITHUB_PATH -Value $binDirectory -Encoding utf8" in installer
    assert "Resolved FFmpeg directory contains a newline" in installer


def test_production_compose_forces_production_and_disables_runtime_migrations() -> None:
    compose = (DEPLOYMENT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert compose.count("AIVF_ENV: production") == 3
    assert compose.count('AIVF_ALLOW_RUNTIME_MIGRATIONS: "0"') == 3
    assert 'AIVF_COOKIE_SECURE: "1"' in compose
    assert "${AIVF_ENV:-production}" not in compose
    assert "${AIVF_COOKIE_SECURE:-1}" not in compose


def test_windows_production_launcher_is_separate_and_fail_closed() -> None:
    launcher = (REPOSITORY_ROOT / "START-PRODUCTION.ps1").read_text(encoding="utf-8")
    wrapper = (REPOSITORY_ROOT / "START-PRODUCTION.bat").read_text(encoding="utf-8")

    assert "docker-compose.yml" in launcher
    assert '$ComposeFile = Join-Path $Root "06-CONFIG-AND-DEPLOYMENT\\docker-compose.yml"' in launcher
    assert "@sha256:[0-9a-fA-F]{64}" in launcher
    assert '$env:AIVF_ENV = "production"' in launcher
    assert '$env:AIVF_COOKIE_SECURE = "1"' in launcher
    assert '$env:AIVF_ALLOW_RUNTIME_MIGRATIONS = "0"' in launcher
    assert "aivf-db-migrate" in launcher
    assert "aivf-backup backup" in launcher
    assert "aivf-backup verify" in launcher
    assert "--no-build" in launcher
    assert "if ($existingImage -ne $image)" in launcher
    assert "START-PRODUCTION.ps1" in wrapper


def test_root_readme_distinguishes_development_and_production_launchers() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "is the development/supervised-local launcher" in readme
    assert "START-PRODUCTION.bat" in readme
    assert "START-PRODUCTION.ps1 -Migrate" in readme
    assert "does not build source" in readme



def test_docker_build_verifies_model_assets_against_locked_hashes() -> None:
    dockerfile = (DEPLOYMENT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    verify = "python 03-SIDE-CODE/tools/verify_model_lock.py /app"
    generated_sums = "sha256sum /app/.models/mobilenet_ssd/deploy.prototxt"
    generated_manifest = "python 03-SIDE-CODE/tools/generate_model_manifest.py"

    assert "AIVF_CONFIG_PATH=/app/state/aivf_config.json" in dockerfile
    assert verify in dockerfile
    assert dockerfile.index(verify) < dockerfile.index(generated_sums)
    assert dockerfile.index(generated_sums) < dockerfile.index(generated_manifest)



def test_admin_unlock_code_is_environment_only_and_compose_passes_it() -> None:
    application = (REPOSITORY_ROOT / "02-WEB-FILES" / "app" / "web_app_v3.py").read_text(encoding="utf-8")
    launcher = (REPOSITORY_ROOT / "START-ALL.ps1").read_text(encoding="utf-8")

    assert 'os.environ.get("AIVF_ADMIN_PANEL_CODE", "").strip()' in application
    assert 'os.environ.get("AIVF_ADMIN_PANEL_CODE", "2026")' not in application
    assert 'if ([string]::IsNullOrWhiteSpace((Get-EnvValue "AIVF_ADMIN_PANEL_CODE")))' in launcher
    assert 'Set-EnvValue "AIVF_ADMIN_PANEL_CODE" $adminPanelCode' in launcher
    assert "Admin unlock code saved in the private .env file; it is deliberately not printed." in launcher
    assert "Private admin-console unlock code (save it somewhere safe): $adminPanelCode" not in launcher

    for compose_name in ("docker-compose.yml", "docker-compose.dev.yml"):
        compose = (DEPLOYMENT_ROOT / compose_name).read_text(encoding="utf-8")
        assert "AIVF_ADMIN_PANEL_CODE: ${AIVF_ADMIN_PANEL_CODE:-}" in compose


def test_windows_launcher_never_prints_secrets_and_validates_compose_quietly() -> None:
    launcher = (REPOSITORY_ROOT / "START-ALL.ps1").read_text(encoding="utf-8")
    assert '"config",' in launcher and '"--quiet"' in launcher
    assert "Write-Host $token" not in launcher
    assert "Private admin-console unlock code (save it somewhere safe): $adminPanelCode" not in launcher
    assert "Named dashboard accounts are configured; shared dashboard token login is disabled." in launcher



def test_windows_startup_uses_fullscreen_splash_and_keeps_failure_console_open() -> None:
    launcher = (REPOSITORY_ROOT / "START-ALL.ps1").read_text(encoding="utf-8")
    batch = (REPOSITORY_ROOT / "START-ALL.bat").read_text(encoding="utf-8")
    splash = (REPOSITORY_ROOT / "02-WEB-FILES" / "static" / "startup-splash.html").read_text(encoding="utf-8")
    welcome = (REPOSITORY_ROOT / "02-WEB-FILES" / "templates" / "welcome.html").read_text(encoding="utf-8")

    assert "Start-StartupSplash" in launcher
    assert '"--kiosk"' in launcher
    assert '"--start-fullscreen"' in launcher
    assert '"--app=$splashUri"' in launcher
    assert "EditFactoryV3-Kiosk-" in launcher
    assert 'Get-Content -LiteralPath $welcomeSource -Encoding UTF8 -Raw' in launcher
    assert 'Get-Content -LiteralPath $splashSource -Encoding UTF8 -Raw' in launcher
    assert 'Start-Process "$BaseUrl/login"' not in launcher
    assert 'pause' in batch
    assert 'if not "%EXIT_CODE%"=="0" (' in batch
    assert '__AIVF_BASE_URL__' in splash
    assert '__AIVF_WELCOME_URI__' in splash
    assert 'minimumSplashMs = 4200' in splash
    assert 'fetch(baseUrl + "/api/health"' in splash
    assert 'window.location.replace(welcomeUri)' in splash
    assert "$welcomeHtml = (Get-Content -LiteralPath $welcomeSource -Encoding UTF8 -Raw)" in launcher
    assert 'href="/login"' in welcome

    production_launcher = (REPOSITORY_ROOT / "START-PRODUCTION.ps1").read_text(encoding="utf-8")
    production_batch = (REPOSITORY_ROOT / "START-PRODUCTION.bat").read_text(encoding="utf-8")
    assert "Start-StartupSplash" in production_launcher
    assert '"--kiosk"' in production_launcher
    assert '"--start-fullscreen"' in production_launcher
    assert '"--app=$splashUri"' in production_launcher
    assert "EditFactoryV3-Kiosk-" in production_launcher
    assert 'Get-Content -LiteralPath $welcomeSource -Encoding UTF8 -Raw' in production_launcher
    assert 'Start-Process "$baseUrl/login"' not in production_launcher
    assert 'if not "%EXIT_CODE%"=="0" (' in production_batch
