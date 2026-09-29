from __future__ import annotations

from pathlib import Path

import pytest

from ai_video_factory.asset_manager import AssetManager


def test_assets_uri_cannot_escape_root(tmp_path: Path) -> None:
    manager = AssetManager(str(tmp_path / "assets"))
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    assert manager.resolve("assets://../outside.txt") is None
    assert manager.resolve(r"assets://..\outside.txt") is None


def test_import_asset_rejects_traversal_category(tmp_path: Path) -> None:
    manager = AssetManager(str(tmp_path / "assets"))
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    with pytest.raises(ValueError):
        manager.import_asset(str(source), "../escape")
    with pytest.raises(ValueError):
        manager.import_asset(str(source), "video", "..")


def test_import_asset_sanitizes_filename(tmp_path: Path) -> None:
    manager = AssetManager(str(tmp_path / "assets"))
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"fake")
    uri = manager.import_asset(str(source), "video")
    assert uri == "assets://video/clip.mp4"
    assert (tmp_path / "assets" / "video" / "clip.mp4").is_file()
