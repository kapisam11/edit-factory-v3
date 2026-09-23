from pathlib import Path

from PIL import Image

from ai_video_factory.thumbnail import make_thumbnail, make_thumbnail_variants


def test_thumbnail_uses_real_background_and_readable_composition(tmp_path: Path):
    background = tmp_path / "background.jpg"
    output = tmp_path / "thumbnail.png"

    image = Image.new("RGB", (1920, 1080), (30, 70, 150))
    draw = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).ImageDraw(image)
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


def test_thumbnail_fallback_is_still_valid(tmp_path: Path):
    output = tmp_path / "fallback.png"
    make_thumbnail("A Better Ending", str(output))

    with Image.open(output) as result:
        assert result.size == (1280, 720)
        assert result.getbbox() is not None
