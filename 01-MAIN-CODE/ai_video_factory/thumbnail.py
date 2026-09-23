"""Professional thumbnail generation with creator style learning.

Uses learned color palettes, contrast levels, and composition patterns
from top-performing videos in the same niche.

Requires: pillow
"""
import logging
import math
import os
import re
import shutil
import subprocess
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




def _fit_crop(img: Image.Image, size: tuple, focus=(0.68, 0.5)) -> Image.Image:
    """Crop an image to an exact thumbnail size while preserving a focal area."""
    target_w, target_h = size
    src_w, src_h = img.size
    if src_w <= 0 or src_h <= 0:
        raise ValueError("background image has invalid dimensions")

    target_ratio = target_w / target_h
    source_ratio = src_w / src_h
    if source_ratio > target_ratio:
        crop_h = src_h
        crop_w = int(src_h * target_ratio)
    else:
        crop_w = src_w
        crop_h = int(src_w / target_ratio)

    fx = min(1.0, max(0.0, float(focus[0]))) * src_w
    fy = min(1.0, max(0.0, float(focus[1]))) * src_h
    left = int(fx - crop_w / 2)
    top = int(fy - crop_h / 2)
    left = max(0, min(left, src_w - crop_w))
    top = max(0, min(top, src_h - crop_h))
    return img.crop((left, top, left + crop_w, top + crop_h)).resize(size, Image.Resampling.LANCZOS)


def _score_image(path: str) -> float:
    """Prefer sharp, moderately bright, colorful frames over flat/dark frames."""
    try:
        img = Image.open(path).convert("RGB").resize((256, 144))
        pixels = list(img.getdata())
        if not pixels:
            return -1.0
        brightness = sum((r + g + b) / 765.0 for r, g, b in pixels) / len(pixels)
        saturation = sum((max(p) - min(p)) / 255.0 for p in pixels) / len(pixels)
        mean = sum(sum(p) / 3.0 for p in pixels) / len(pixels)
        variance = sum(((sum(p) / 3.0) - mean) ** 2 for p in pixels) / len(pixels)
        contrast = min(1.0, math.sqrt(variance) / 64.0)
        return round(
            0.35 * contrast
            + 0.30 * saturation
            + 0.20 * (1.0 - abs(brightness - 0.52) / 0.52)
            + 0.15 * min(1.0, variance / 1800.0),
            6,
        )
    except Exception:
        return -1.0


def extract_best_video_frame(video_path: str, out_dir: str, count: int = 7) -> Optional[str]:
    """Extract several candidate frames and return the strongest thumbnail frame."""
    source = os.path.abspath(video_path)
    if not os.path.isfile(source):
        return None
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return None

    os.makedirs(out_dir, exist_ok=True)
    duration = 0.0
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", source],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = float(probe.stdout.strip())
    except Exception:
        try:
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", source],
                capture_output=True,
                text=True,
                check=True,
            )
            duration = float(probe.stdout.strip())
        except Exception:
            return None

    if not math.isfinite(duration) or duration <= 0:
        return None

    candidates = []
    fractions = [0.10, 0.22, 0.35, 0.50, 0.65, 0.78, 0.90][:max(1, int(count))]
    for idx, fraction in enumerate(fractions, start=1):
        timestamp = max(0.0, min(duration - 0.05, duration * fraction))
        frame_path = os.path.join(out_dir, f".thumbnail_frame_{idx}.jpg")
        try:
            subprocess.run(
                [
                    ffmpeg, "-y", "-ss", f"{timestamp:.3f}", "-i", source,
                    "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "2", frame_path,
                ],
                capture_output=True,
                check=True,
            )
            score = _score_image(frame_path)
            if score >= 0:
                candidates.append((score, frame_path))
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _prepare_thumbnail_text(subject: str, max_words: int = 5) -> str:
    text = re.sub(r"\s+", " ", str(subject or "").strip())
    text = re.sub(r"[|•]+", " ", text)
    words = [word for word in text.split() if word]
    if not words:
        return "WATCH THIS"
    if len(words) > max_words:
        words = words[:max_words]
    return " ".join(words).upper()


def _wrap_thumbnail_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    words = text.split()
    if len(words) <= 2:
        return [" ".join(words)]

    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        width = draw.textbbox((0, 0), candidate, font=font)[2]
        if current and width > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)

    if len(lines) <= 2:
        return lines

    # Keep the design at two lines max. The caller already caps the word count.
    first = " ".join(lines[:-1])
    return [first, lines[-1]]


def _load_background(background_path: Optional[str], size: tuple, focus=(0.68, 0.5)) -> Image.Image:
    if background_path and os.path.isfile(background_path):
        try:
            img = Image.open(background_path).convert("RGB")
            img = _fit_crop(img, size, focus=focus)
            img = ImageEnhance.Color(img).enhance(1.12)
            img = ImageEnhance.Contrast(img).enhance(1.08)
            return img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=110, threshold=3))
        except Exception as exc:
            logger.warning("Thumbnail background load failed: %s", exc)

    # Strong, clean fallback when no real visual is available.
    return _create_gradient_background(size, (18, 20, 28), (170, 22, 52))


def _add_thumbnail_overlay(img: Image.Image, width_fraction: float = 0.56) -> Image.Image:
    w, h = img.size
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    panel_w = int(w * width_fraction)
    draw.rectangle((0, 0, panel_w, h), fill=(0, 0, 0, 155))
    draw.rectangle((panel_w, 0, w, h), fill=(0, 0, 0, 35))
    draw.polygon(
        [(int(w * 0.36), 0), (int(w * 0.58), 0), (int(w * 0.42), h), (int(w * 0.20), h)],
        fill=(0, 0, 0, 35),
    )
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _draw_thumbnail_text(img: Image.Image, text: str, primary: tuple, accent: tuple) -> None:
    w, h = img.size
    draw = ImageDraw.Draw(img)

    horizontal = w >= h
    max_width = int(w * (0.52 if horizontal else 0.88))
    font_size = int(h * (0.18 if horizontal else 0.075))
    font_size = max(46, min(font_size, 150))
    font = _get_font(font_size)

    while font_size > 42:
        lines = _wrap_thumbnail_text(draw, text, font, max_width)
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in lines]
        if max(widths, default=0) <= max_width:
            break
        font_size -= 4
        font = _get_font(font_size)

    lines = _wrap_thumbnail_text(draw, text, font, max_width)
    line_gap = max(8, font_size // 8)
    total_height = sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines) + line_gap * (len(lines) - 1)

    x = int(w * (0.07 if horizontal else 0.06))
    y = int((h - total_height) * (0.46 if horizontal else 0.74))
    if horizontal:
        y = max(int(h * 0.16), y)

    # Subtle top label gives the composition a deliberate editorial identity.
    label_font = _get_font(max(24, min(34, int(h * 0.045))))
    label = "EDIT FACTORY"
    label_w = draw.textbbox((0, 0), label, font=label_font)[2]
    draw.rounded_rectangle((x, max(20, y - label_font.size - 16), x + label_w + 28, max(50, y - 8)),
                           radius=10, fill=(0, 0, 0))
    draw.text((x + 14, max(20, y - label_font.size - 13)), label, font=label_font, fill=accent)

    for idx, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_y = y + idx * (font_size + line_gap)
        # Shadow first.
        draw.text((x + 8, line_y + 8), line, font=font, fill=(0, 0, 0))
        # Highlight the final word instead of alternating every word.
        parts = line.split()
        if len(parts) >= 2:
            normal = " ".join(parts[:-1])
            last = parts[-1]
            normal_w = draw.textbbox((0, 0), normal + " ", font=font)[2]
            draw.text((x, line_y), normal, font=font, fill=(248, 248, 248), stroke_width=3, stroke_fill=(0, 0, 0))
            draw.text((x + normal_w, line_y), last, font=font, fill=accent, stroke_width=3, stroke_fill=(0, 0, 0))
        else:
            draw.text((x, line_y), line, font=font, fill=primary, stroke_width=3, stroke_fill=(0, 0, 0))


def make_thumbnail(
    subject: str,
    out_path: str,
    size: tuple = (1280, 720),
    style_profile: Optional[dict] = None,
    background_path: Optional[str] = None,
    background_focus: tuple = (0.68, 0.5),
) -> str:
    """Create a polished thumbnail using a real focal image when available."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    if style_profile:
        colors = style_profile.get("dominant_colors", [])
        style = style_profile.get("recommended_style", "dark_dramatic")
    else:
        colors = ["rgb(220,20,60)", "rgb(10,12,18)", "rgb(255,255,255)"]
        style = "dark_dramatic"

    if colors:
        primary = _parse_rgb(colors[0])
        secondary = _parse_rgb(colors[1]) if len(colors) > 1 else (10, 12, 18)
        accent = _parse_rgb(colors[2]) if len(colors) > 2 else (255, 210, 60)
    else:
        primary, secondary, accent = (220, 20, 60), (10, 12, 18), (255, 210, 60)

    # Avoid unreadable creator palettes.
    if max(accent) < 120:
        accent = (255, 210, 60)
    if max(primary) < 100:
        primary = (255, 255, 255)
    if max(secondary) > 225:
        secondary = (18, 20, 28)

    img = _load_background(background_path, size, focus=background_focus)
    img = _add_thumbnail_overlay(img, width_fraction=0.58 if size[0] >= size[1] else 1.0)

    if size[0] >= size[1]:
        # Add a simple accent edge to make the card feel intentional without clutter.
        draw = ImageDraw.Draw(img)
        edge_w = max(6, int(size[0] * 0.008))
        draw.rectangle((0, 0, edge_w, size[1]), fill=accent)

    text = _prepare_thumbnail_text(subject, max_words=5)
    _draw_thumbnail_text(img, text, primary=(248, 248, 248), accent=accent)

    img.save(out_path, format="PNG", optimize=True)
    logger.info("[THUMB] Created: %s (%s; background=%s)", out_path, style, bool(background_path))
    return out_path

def make_thumbnail_variants(
    subject: str,
    out_dir: str,
    count: int = 3,
    topic: Optional[str] = None,
    background_path: Optional[str] = None,
) -> List[str]:
    """Create distinct, text-safe thumbnail variants around one visual focal point."""
    os.makedirs(out_dir, exist_ok=True)
    style = None
    if topic:
        try:
            style = learn_style(topic)
        except Exception as exc:
            logger.warning("Style learning failed: %s", exc)

    variants = []
    for i in range(max(1, int(count))):
        out = os.path.join(out_dir, f"variant_{i + 1}.png")
        mod_style = style.copy() if style else None
        if i == 0:
            focus = (0.70, 0.50)
        elif i == 1:
            focus = (0.58, 0.46)
        else:
            focus = (0.78, 0.54)
        make_thumbnail(
            subject,
            out,
            style_profile=mod_style,
            background_path=background_path,
            background_focus=focus,
        )
        variants.append(out)
    return variants
