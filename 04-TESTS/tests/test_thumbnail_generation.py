from pathlib import Path

from PIL import Image, ImageDraw

from ai_video_factory.thumbnail import make_thumbnail, make_thumbnail_variants, select_best_thumbnail_variant


def test_thumbnail_uses_real_background_and_readable_composition(tmp_path: Path):
    background = tmp_path / "background.jpg"
    output = tmp_path / "thumbnail.png"

    image = Image.new("RGB", (1920, 1080), (30, 70, 150))
    draw = ImageDraw.Draw(image)
    draw.rectangle((1100, 180, 1800, 900), fill=(240, 160, 30))
    image.save(background)

    make_thumbnail("The Friend Everyone Trusted", str(output), background_path=str(background))

    with Image.open(output) as result:
        assert result.size == (1280, 720)
        assert result.mode == "RGB"
        assert len(result.getcolors(maxcolors=10_000_000)) > 1000

    variants = make_thumbnail_variants(
        "The Friend Everyone Trusted",
        str(tmp_path / "variants"),
        count=3,
        background_path=str(background),
    )
    assert len(variants) == 3
    assert all(Path(path).is_file() for path in variants)

    # A/B variants must test genuinely different compositions, not merely write
    # several filenames for effectively identical thumbnails.
    from PIL import ImageChops

    with Image.open(variants[0]).convert("RGB") as first:
        for path in variants[1:]:
            with Image.open(path).convert("RGB") as other:
                assert ImageChops.difference(first, other).getbbox() is not None

    quality_report = tmp_path / "variants" / "thumbnail_quality.json"
    assert quality_report.is_file()
    import json

    report = json.loads(quality_report.read_text(encoding="utf-8"))
    assert len(report["variants"]) == 3
    assert len(report["ranked"]) == 3


def test_thumbnail_fallback_is_still_valid(tmp_path: Path):
    output = tmp_path / "fallback.png"
    make_thumbnail("A Better Ending", str(output))

    with Image.open(output) as result:
        assert result.size == (1280, 720)
        assert result.getbbox() is not None


def test_thumbnail_selector_honors_modern_quality_gate(monkeypatch):
    paths = ["first.png", "second.png"]
    legacy = {
        path: {"passed": True, "score": 80.0, "checks": {"nonempty": True}}
        for path in paths
    }
    modern = {
        "first.png": {"ok": False, "score": 0.99},
        "second.png": {"ok": True, "score": 0.55},
    }

    monkeypatch.setattr(
        "ai_video_factory.thumbnail.thumbnail_quality_report",
        lambda path: legacy[path],
    )
    monkeypatch.setattr(
        "ai_video_factory.thumbnail.score_thumbnail",
        lambda path: modern[path],
    )

    assert select_best_thumbnail_variant(paths) == 2
