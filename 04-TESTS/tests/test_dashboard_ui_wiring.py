"""Integration checks for dashboard navigation, static assets, and real actions."""
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "02-WEB-FILES"
TEMPLATE = WEB_ROOT / "templates" / "index.html"
HEADER = WEB_ROOT / "templates" / "dashboard_header.html"


def test_dashboard_static_assets_and_packaged_pages_resolve() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    header = HEADER.read_text(encoding="utf-8")
    combined = html + "\n" + header
    assets = re.findall(r"""(?:href|src)=["']/static/([^"']+)["']""", combined)

    assert "css/dashboard-professional.css" in assets
    assert "js/dashboard_enhancements.js" in assets
    assert "js/dashboard_navigation.js" in assets
    for asset in assets:
        assert (WEB_ROOT / "static" / asset).is_file(), f"Missing dashboard asset: {asset}"

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for template in ("dashboard_header.html", "jobs.html", "job_detail.html", "library.html", "settings.html", "admin.html"):
        assert f"02-WEB-FILES/templates/{template}" in pyproject
        assert (WEB_ROOT / "templates" / template).is_file()
    for script in ("dashboard_navigation.js", "jobs_page.js", "job_detail_page.js", "library_page.js", "settings_page.js", "admin_page.js"):
        assert f"02-WEB-FILES/static/js/{script}" in pyproject


def test_top_navigation_opens_dedicated_pages_not_in_page_anchors() -> None:
    header = HEADER.read_text(encoding="utf-8")
    navigation = header.split('<nav class="dashboard-nav"', 1)[1].split("</nav>", 1)[0]
    targets = re.findall(r'href="([^"]+)"', navigation)

    assert targets == ["/", "/jobs", "/library", "/settings", "/autonomous"]
    assert not any(target.startswith("#") for target in targets)
    assert 'href="/autonomous"' in header
    assert "{% if aivf_role == 'admin' %}" in header
    assert 'id="brandAdminTrigger"' in header
    assert 'id="adminUnlockForm"' in header
    assert 'src="/static/js/dashboard_navigation.js"' in header
    # The advanced endpoint is intentionally not exposed as a normal navigation link.
    assert 'href="/admin"' not in navigation


def test_header_actions_and_dashboard_controls_use_existing_backend() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    header = HEADER.read_text(encoding="utf-8")
    inline_script = html.split("<script nonce=", 1)[1].split("</script>", 1)[0]
    external_script = (WEB_ROOT / "static" / "js" / "dashboard_enhancements.js").read_text(encoding="utf-8")
    defined = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", inline_script + "\n" + external_script))

    for action in ("refreshPublishingStatus", "loadPackages", "savePresetPrompt", "showPresetHistory", "cancelCurrentJob", "saveSettings", "switchTab", "saveScript", "openPackage"):
        assert action in defined, f"Dashboard handler is missing: {action}"

    assert '<form action="/logout" method="post" class="logout-form">' in header
    assert '<a class="btn-secondary header-settings" href="/settings">Settings</a>' in header
    assert "fetch('/api/run'" in html
    assert "fetch('/api/jobs'" in html
    assert "fetch('/api/queue/status'" in html
    assert "fetch('/api/publishing/status'" in external_script
    assert "window.refreshDashboardPublishingStatus = refreshPublishingStatus" in external_script
    assert "classList.toggle('active', candidate === link)" not in inline_script


def test_new_pages_use_real_backends_and_admin_route_is_server_protected() -> None:
    jobs = (WEB_ROOT / "static" / "js" / "jobs_page.js").read_text(encoding="utf-8")
    library = (WEB_ROOT / "static" / "js" / "library_page.js").read_text(encoding="utf-8")
    settings = (WEB_ROOT / "static" / "js" / "settings_page.js").read_text(encoding="utf-8")
    admin = (WEB_ROOT / "static" / "js" / "admin_page.js").read_text(encoding="utf-8")
    source = (WEB_ROOT / "app" / "web_app_v3.py").read_text(encoding="utf-8")

    assert "fetch('/api/jobs'" in jobs
    assert "fetch('/api/queue/status'" in jobs
    assert "fetch('/api/packages'" in library
    assert "fetch('/api/settings'" in settings
    assert "fetch('/api/admin/diagnostics'" in admin
    assert '@app.post("/api/admin/unlock")' in source
    assert '@app.get("/api/admin/diagnostics")' in source
    assert "_require_admin_panel_role()" in source
    assert "_admin_panel_is_unlocked()" in source


def test_dashboard_navigation_signout_and_settings_access_are_obvious() -> None:
    header = HEADER.read_text(encoding="utf-8")
    assert '<a href="/jobs"' in header
    assert '<a href="/library"' in header
    assert '<a href="/settings"' in header
    assert '<form action="/logout" method="post" class="logout-form">' in header
    assert "aivf_user_id|e" in header
