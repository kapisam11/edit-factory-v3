"""Professional thumbnail generation with creator style learning.

Uses learned color palettes, contrast levels, and composition patterns
from top-performing videos in the same niche.

Requires: pillow
"""
import logging
import os
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

from .style_learner import learn_style

logger = logging.getLogger(__name__)


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _parse_rgb(s: str):
    """Parse 'rgb(220,20,60)' → (220, 20, 60)"""
    import re
    m = re.search(r"rgb\((\d+),(\d+),(\d+)\)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (255, 0, 0)


def _get_font(size: int):
    """Try to load a bold font, fallback to default."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _create_gradient_background(size: tuple, color1, color2, direction="diagonal"):
    """Create a smooth gradient background."""
    w, h = size
    base = Image.new("RGB", size, color1)
    draw = ImageDraw.Draw(base)
    for y in range(h):
        ratio = y / h
        r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
        g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
        b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return base


def _add_vignette(img: Image.Image, strength: float = 0.4) -> Image.Image:
    """Add dark vignette around edges for cinematic look."""
    w, h = img.size
    vignette = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(vignette)
    # Draw concentric rectangles fading to transparent
    for i in range(int(min(w, h) / 2), 0, -10):
        alpha = int(255 * (1 - (i / (min(w, h) / 2))) * strength)
        draw.rectangle([i, i, w - i, h - i], outline=alpha)
    vignette = vignette.filter(ImageFilter.GaussianBlur(radius=min(w, h) // 8))
    # Composite
    img = img.copy()
    # Darken edges
    overlay = Image.new("RGB", (w, h), (0, 0, 0))
    img = Image.blend(img, overlay, strength)
    return img


def _add_text_with_outline(draw, text, pos, font, fill, outline_width=3, outline_color=(0,0,0)):
    """Draw text with thick outline for readability."""
    x, y = pos
    # Draw outline
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
    # Draw main text
    draw.text((x, y), text, font=font, fill=fill)


def make_thumbnail(
    subject: str,
    out_path: str,
    size: tuple = (1280, 720),
    style_profile: Optional[dict] = None,
) -> str:
    """Create a professional thumbnail with learned style.

    Args:
        subject: Thumbnail text (2-5 words recommended)
        out_path: Output file path
        size: Image dimensions
        style_profile: Optional style profile from style_learner.learn_style()
    """
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # Determine style
    if style_profile:
        brightness = style_profile.get("avg_brightness", 0.3)
        saturation = style_profile.get("avg_saturation", 0.6)
        contrast = style_profile.get("avg_contrast", 0.15)
        colors = style_profile.get("dominant_colors", [])
        style = style_profile.get("recommended_style", "dark_dramatic")
    else:
        brightness, saturation, contrast = 0.3, 0.6, 0.15
        colors = ["rgb(220,20,60)", "rgb(0,0,0)", "rgb(255,255,255)"]
        style = "dark_dramatic"

    # Parse colors
    if colors:
        primary = _parse_rgb(colors[0])
        secondary = _parse_rgb(colors[1]) if len(colors) > 1 else (0, 0, 0)
        accent = _parse_rgb(colors[2]) if len(colors) > 2 else (255, 255, 255)
    else:
        primary, secondary, accent = (220, 20, 60), (0, 0, 0), (255, 255, 255)

    # Adjust for dark/bright style
    if "bright" in style:
        bg1, bg2 = (255, 255, 240), (240, 240, 255)
        text_color = (20, 20, 20)
    else:
        bg1, bg2 = secondary, primary
        text_color = accent

    # Create gradient background
    img = _create_gradient_background(size, bg1, bg2)

    # Add subtle noise/texture for realism
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.0 + contrast)
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1.0 + saturation)

    # Vignette
    img = _add_vignette(img, strength=0.3 if "bright" in style else 0.5)

    # Draw text
    draw = ImageDraw.Draw(img)
    words = subject.split()

    # Font sizing based on word count
    if len(words) <= 2:
        font_size = 120
    elif len(words) <= 4:
        font_size = 90
    else:
        font_size = 70

    font = _get_font(font_size)

    # Layout: center text, keep in safe zone (away from edges)
    safe_margin = int(size[1] * 0.15)
    y_start = safe_margin + 20

    for i, word in enumerate(words):
        # Uppercase for impact
        text = word.upper()
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        x = (size[0] - text_w) // 2
        y = y_start + i * (font_size + 15)

        # Alternate colors for emphasis
        fill = text_color if i % 2 == 0 else primary
        _add_text_with_outline(draw, text, (x, y), font, fill, outline_width=4)

    # Save
    img.save(out_path)
    logger.info("[THUMB] Created: %s (%s)", out_path, style)
    return out_path


def make_thumbnail_variants(subject: str, out_dir: str, count: int = 3, topic: Optional[str] = None) -> List[str]:
    """Create multiple thumbnail variants with different styles.

    If topic is provided, learns from top creators first.
    """
    os.makedirs(out_dir, exist_ok=True)
    style = None
    if topic:
        try:
            style = learn_style(topic)
        except Exception as e:
            logger.warning("Style learning failed: %s", e)

    variants = []
    for i in range(count):
        out = os.path.join(out_dir, f"variant_{i+1}.png")
        # Slightly vary the style per variant
        if style:
            # Modify saturation/contrast for variety
            mod_style = style.copy()
            mod_style["avg_saturation"] = min(1.0, style.get("avg_saturation", 0.6) + (i - 1) * 0.1)
            mod_style["avg_contrast"] = min(0.5, style.get("avg_contrast", 0.15) + (i - 1) * 0.05)
        else:
            mod_style = None
        make_thumbnail(subject, out, style_profile=mod_style)
        variants.append(out)
    return variants


def make_thumbnail_vertical(subject: str, out_path: str, size: tuple = (1080, 1920)) -> str:
    """Create a vertical thumbnail optimized for Shorts/Reels."""
    return make_thumbnail(subject, out_path, size=size)
