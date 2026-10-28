"""Regression checks for the startup experience and local credentials helper."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_access_details_viewer_shows_only_relevant_local_credentials() -> None:
    viewer = (ROOT / "SHOW-LOCAL-CREDENTIALS.ps1").read_text(encoding="utf-8")

    assert 'Get-ConfiguredValue "AIVF_DASHBOARD_TOKEN"' in viewer
    assert 'Get-ConfiguredValue "AIVF_ADMIN_PANEL_CODE"' in viewer
    assert 'Get-ConfiguredValue "AIVF_DASHBOARD_USERS"' in viewer
    assert "FLASK_SECRET_KEY" not in viewer
    assert "$tokenBox.UseSystemPasswordChar = $true" in viewer
    assert "$adminBox.UseSystemPasswordChar = $true" in viewer
    assert "[System.Windows.Forms.Clipboard]::SetText" in viewer
    assert "SHOW-LOCAL-CREDENTIALS.bat" in viewer
    assert "http://127.0.0.1:" in viewer


def test_both_windows_launchers_open_access_details_outside_the_command_window() -> None:
    for name in ("START-ALL.ps1", "START-PRODUCTION.ps1"):
        launcher = (ROOT / name).read_text(encoding="utf-8")
        assert "function Start-LocalAccessDetails" in launcher
        assert 'Join-Path $Root "SHOW-LOCAL-CREDENTIALS.ps1"' in launcher
        assert "Start-Process -FilePath $powershellCommand.Source" in launcher
        assert "Start-LocalAccessDetails" in launcher
        assert "Write-Host $dashboardToken" not in launcher

    batch = (ROOT / "SHOW-LOCAL-CREDENTIALS.bat").read_text(encoding="utf-8")
    assert 'start "" powershell.exe' in batch
    assert "-WindowStyle Hidden" in batch
    assert "SHOW-LOCAL-CREDENTIALS.ps1" in batch


def test_startup_splash_has_live_status_and_preserves_transition_contract() -> None:
    splash = (
        ROOT / "02-WEB-FILES" / "static" / "startup-splash.html"
    ).read_text(encoding="utf-8")

    assert "__AIVF_BASE_URL__" in splash
    assert "__AIVF_WELCOME_URI__" in splash
    assert 'minimumSplashMs = 4200' in splash
    assert 'fetch(baseUrl + "/api/health"' in splash
    assert 'window.location.replace(welcomeUri)' in splash
    assert 'id="status-detail"' in splash
    assert 'id="engine-state-text"' in splash
    assert "Make something" in splash
