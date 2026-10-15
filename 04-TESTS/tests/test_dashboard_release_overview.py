"""Checks for the dashboard's user-facing reliability and release overview."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "02-WEB-FILES" / "templates" / "index.html"
STYLES = ROOT / "02-WEB-FILES" / "static" / "css" / "enhancements.css"
GUIDE = ROOT / "00-INFO" / "04-USING-THE-DASHBOARD.md"


def test_dashboard_uses_edit_factory_v3_branding_and_shows_release_overview() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    assert "<title>Edit Factory V3 — Production Dashboard</title>" in html
    assert "<h1>EDIT FACTORY V3</h1>" in html
    assert "Latest reliability &amp; production updates" in html
    assert html.count('class="release-card"') == 8


def test_overview_covers_the_recent_operational_additions() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    for phrase in (
        "Protect valid video output",
        "Recoverable background work",
        "Readiness reports tell the truth",
        "START-PRODUCTION.bat",
        "Conservative resource defaults",
        "Per-platform status and reconciliation",
        "Evidence before publishing",
        "More Windows and pipeline checks",
        "<code>PASS</code>",
        "<code>IMPLEMENTED</code>",
        "<code>ENVIRONMENT</code>",
        "not a live certification",
    ):
        assert phrase in html, phrase


def test_external_overview_links_have_safe_new_tab_attributes() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")

    assert html.count('target="_blank" rel="noopener noreferrer"') == 8
    assert "https://github.com/kapisam11/edit-factory-v3/actions" in html
    assert "00-INFO/UPLOAD-PACKAGE.md" in html


def test_release_overview_has_responsive_styling_and_documentation() -> None:
    css = STYLES.read_text(encoding="utf-8")
    guide = GUIDE.read_text(encoding="utf-8")

    assert ".release-overview-grid" in css
    assert "@media (max-width: 1100px)" in css
    assert "@media (max-width: 640px)" in css
    assert "static feature overview" in guide
    assert "not a live release certificate" in guide
    assert "confirm a remote post" in guide
