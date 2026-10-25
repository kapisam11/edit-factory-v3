from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_publisher_task_uses_a_network_capable_default_logon() -> None:
    script = (
        ROOT
        / "03-SIDE-CODE"
        / "scripts"
        / "windows"
        / "publisher"
        / "INSTALL-PUBLISHER-TASK.ps1"
    ).read_text(encoding="utf-8")
    assert "-LogonType Password" in script
    assert "-LogonType S4U" not in script
    assert "-Password $TaskPassword" in script
    assert "Credential username must match the current Windows identity" in script
    assert "[switch]$UseInteractiveLogon" in script


def test_interactive_mode_documents_its_unattended_startup_limit() -> None:
    script = (
        ROOT
        / "03-SIDE-CODE"
        / "scripts"
        / "windows"
        / "publisher"
        / "INSTALL-PUBLISHER-TASK.ps1"
    ).read_text(encoding="utf-8")
    docs = (ROOT / "00-INFO" / "UPLOAD-PACKAGE.md").read_text(encoding="utf-8")
    assert "Interactive mode will not run before login" in script
    assert "pre-login recovery" in docs



def test_relocated_publisher_scripts_find_repo_root_and_runner() -> None:
    root = ROOT / "03-SIDE-CODE" / "scripts" / "windows" / "publisher"
    installer = (root / "INSTALL-PUBLISHER-TASK.ps1").read_text(encoding="utf-8")
    runner = (root / "RUN-PUBLISHER.ps1").read_text(encoding="utf-8")
    assert "function Get-EditFactoryRepositoryRoot" in installer
    assert '$Runner = Join-Path $PSScriptRoot "RUN-PUBLISHER.ps1"' in installer
    assert "function Get-EditFactoryRepositoryRoot" in runner
