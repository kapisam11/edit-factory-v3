"""Static integration checks for dashboard assets, navigation and real UI handlers."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "02-WEB-FILES"
TEMPLATE = WEB_ROOT / "templates" / "index.html"


def test_dashboard_static_assets_resolve() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    assets = re.findall(r"""(?:href|src)=["']/static/([^"']+)["']""", html)

    assert "css/dashboard-professional.css" in assets
    assert "js/dashboard_enhancements.js" in assets
    assert assets, "The dashboard should declare its stylesheet and script assets."
    for asset in assets:
        assert (WEB_ROOT / "static" / asset).is_file(), f"Missing dashboard asset: {asset}"


def test_dashboard_navigation_targets_real_sections() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    navigation = html.split('<nav class="dashboard-nav"', 1)[1].split("</nav>", 1)[0]
    targets = re.findall(r'href="#([^"]+)"', navigation)
    ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', html))

    assert targets
    assert set(targets) <= ids
    assert {"new-production", "job-queue", "live-output", "generated-packages", "publishingPanel"} <= ids


def test_header_controls_use_existing_dashboard_actions() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    inline_script = html.split("<script nonce=", 1)[1].split("</script>", 1)[0]
    external_script = (WEB_ROOT / "static" / "js" / "dashboard_enhancements.js").read_text(encoding="utf-8")
    defined = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", inline_script + "\n" + external_script))

    for action in ("openSettings", "refreshPublishingStatus", "loadPackages", "savePresetPrompt", "showPresetHistory", "cancelCurrentJob", "saveSettings", "switchTab", "saveScript", "openPackage"):
        assert action in defined, f"Dashboard handler is missing: {action}"

    assert '<form action="/logout" method="post" class="logout-form">' in html
    assert "fetch('/api/run'" in html
    assert "fetch('/api/jobs'" in html
    assert "fetch('/api/queue/status'" in html
    assert "fetch('/api/publishing/status'" in html


def test_dashboard_navigation_signout_does_not_expose_mutating_actions() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    assert '<a href="#new-production" class="nav-link active">Create</a>' in html
    assert '<a href="#job-queue" class="nav-link">Job queue</a>' in html
    assert 'method="post" class="logout-form"' in html
    assert "aivf_user_id|e" in html
