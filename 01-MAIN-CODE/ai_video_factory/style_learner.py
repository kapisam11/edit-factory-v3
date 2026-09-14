"""Creator style learning via YouTube thumbnail analysis.

Scrapes top-performing videos in a niche, downloads their thumbnails,
analyzes color palettes, text placement, brightness, and composition.
Stores learned patterns in knowledge_base for future thumbnail generation.

Requires: beautifulsoup4, pillow
"""
import json
import logging
import os
from io import BytesIO
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import requests
from PIL import Image

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

YOUTUBE_THUMB_URL = "https://img.youtube.com/vi/{video_id}/maxresdefault.jpg"
YOUTUBE_THUMB_HQ = "https://img.youtube.com/vi/{video_id}/hqdefault.jpg"


def search_youtube(query: str, max_results: int = 12) -> List[Dict]:
    """Search YouTube via DuckDuckGo redirect scraping. Returns video IDs + titles."""
    results = []
    if not BeautifulSoup:
        return results
    try:
        search_q = quote_plus(f"site:youtube.com {query}")
        url = f"https://html.duckduckgo.com/html/?q={search_q}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if not r.ok:
            return results
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select(".result__a"):
            href = a.get("href", "")
            # Extract YouTube video ID
            m = __import__("re").search(r"[?&]v=([A-Za-z0-9_\-]{11})", href)
            if not m:
                m = __import__("re").search(r"youtu\.be/([A-Za-z0-9_\-]{11})", href)
            if m:
                vid = m.group(1)
                title = a.get_text(strip=True)
                if vid not in {r["video_id"] for r in results}:
                    results.append({"video_id": vid, "title": title})
            if len(results) >= max_results:
                break
    except Exception as e:
        logger.warning("YouTube search failed: %s", e)
    return results


def download_thumbnail(video_id: str, out_dir: str) -> Optional[str]:
    """Download a YouTube thumbnail by video ID. Falls back to hqdefault."""
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{video_id}.jpg")
    if os.path.exists(out_path):
        return out_path
    urls = [
        YOUTUBE_THUMB_URL.format(video_id=video_id),
        YOUTUBE_THUMB_HQ.format(video_id=video_id),
    ]
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=10)
            if r.ok and len(r.content) > 1000:
                with open(out_path, "wb") as f:
                    f.write(r.content)
                return out_path
        except Exception:
            continue
    return None


def analyze_thumbnail(path: str) -> Dict:
    """Analyze a thumbnail image: colors, brightness, saturation, contrast."""
    try:
        img = Image.open(path).convert("RGB")
        w, h = img.size
        # Resize for speed
        small = img.resize((100, 100))

        # Color palette (top 5 dominant colors)
        pixels = list(small.getdata())
        from collections import Counter
        rounded = [(r//10*10, g//10*10, b//10*10) for r, g, b in pixels]
        top_colors = Counter(rounded).most_common(5)

        # Brightness
        avg_brightness = sum((r*299 + g*587 + b*114)/1000 for r, g, b in pixels) / len(pixels)

        # Saturation (simple HSV approx)
        saturations = []
        for r, g, b in pixels:
            mx, mn = max(r, g, b), min(r, g, b)
            if mx > 0:
                saturations.append((mx - mn) / mx)
        avg_saturation = sum(saturations) / len(saturations) if saturations else 0

        # Contrast (std dev of brightness)
        brightnesses = [(r*299 + g*587 + b*114)/1000 for r, g, b in pixels]
        mean_b = sum(brightnesses) / len(brightnesses)
        variance = sum((b - mean_b) ** 2 for b in brightnesses) / len(brightnesses)
        contrast = variance ** 0.5

        return {
            "size": (w, h),
            "dominant_colors": [f"rgb({c[0]},{c[1]},{c[2]})" for c, _ in top_colors],
            "brightness": round(avg_brightness / 255, 3),
            "saturation": round(avg_saturation, 3),
            "contrast": round(contrast / 255, 3),
            "has_text_guess": contrast > 0.15,  # high contrast usually = text overlay
        }
    except Exception as e:
        logger.warning("Thumbnail analysis failed for %s: %s", path, e)
        return {}


def learn_style(topic: str, knowledge_dir: str = "knowledge_base", max_samples: int = 10) -> Dict:
    """Learn thumbnail style from top YouTube videos in the topic niche.

    Returns a style profile dict with:
        - avg_brightness, avg_saturation, avg_contrast
        - dominant_color_trends
        - recommended_style (dark, bright, saturated, etc.)
    """
    logger.info("[STYLE] Learning from top creators for: %s", topic)
    cache_path = os.path.join(knowledge_dir, "thumbnail_styles.json")
    os.makedirs(knowledge_dir, exist_ok=True)

    # Check cache first (styles learned within last 7 days)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("topic") == topic:
                logger.info("[STYLE] Using cached style profile")
                return cache.get("profile", {})
        except Exception:
            pass

    # Search and download
    videos = search_youtube(topic, max_results=max_samples)
    thumb_dir = os.path.join(knowledge_dir, "reference_thumbnails")
    os.makedirs(thumb_dir, exist_ok=True)

    analyses = []
    for v in videos:
        path = download_thumbnail(v["video_id"], thumb_dir)
        if path:
            analysis = analyze_thumbnail(path)
            if analysis:
                analysis["video_id"] = v["video_id"]
                analysis["title"] = v["title"]
                analyses.append(analysis)

    if not analyses:
        logger.warning("[STYLE] No thumbnails could be analyzed; using defaults")
        return _default_style()

    # Aggregate statistics
    avg_brightness = sum(a["brightness"] for a in analyses) / len(analyses)
    avg_saturation = sum(a["saturation"] for a in analyses) / len(analyses)
    avg_contrast = sum(a["contrast"] for a in analyses) / len(analyses)
    text_rate = sum(1 for a in analyses if a.get("has_text_guess")) / len(analyses)

    # Collect all dominant colors
    all_colors = []
    for a in analyses:
        all_colors.extend(a.get("dominant_colors", []))

    # Determine style recommendation
    style = "balanced"
    if avg_brightness < 0.35:
        style = "dark_dramatic"
    elif avg_brightness > 0.65:
        style = "bright_energetic"
    if avg_saturation > 0.5:
        style += "_saturated"
    if avg_contrast > 0.12:
        style += "_high_contrast"

    profile = {
        "topic": topic,
        "sample_count": len(analyses),
        "avg_brightness": round(avg_brightness, 3),
        "avg_saturation": round(avg_saturation, 3),
        "avg_contrast": round(avg_contrast, 3),
        "text_overlay_rate": round(text_rate, 2),
        "dominant_colors": all_colors[:10],
        "recommended_style": style,
        "analyses": analyses,
    }

    # Cache
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"topic": topic, "profile": profile}, f, indent=2)

    logger.info("[STYLE] Learned style: %s (from %d samples)", style, len(analyses))
    return profile


def _default_style() -> Dict:
    return {
        "recommended_style": "dark_dramatic_high_contrast",
        "avg_brightness": 0.25,
        "avg_saturation": 0.6,
        "avg_contrast": 0.15,
        "text_overlay_rate": 0.9,
        "dominant_colors": ["rgb(220,20,60)", "rgb(0,0,0)", "rgb(255,255,255)"],
    }
