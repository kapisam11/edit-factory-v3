"""Deterministic thumbnail quality scoring and variant selection."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageStat


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def score_thumbnail(path: str | Path, *, text_safe_fraction: float = 0.55) -> dict[str, float | bool]:
    target = Path(path)
    with Image.open(target) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        gray = rgb.convert("L")
        stat = ImageStat.Stat(gray)
        contrast = _clamp(stat.stddev[0] / 64.0)

        # Measure whether the intended text-safe region has enough luminance spread.
        safe_width = max(1, min(width, int(width * float(text_safe_fraction))))
        safe = gray.crop((0, 0, safe_width, height))
        safe_stddev = ImageStat.Stat(safe).stddev[0]
        text_contrast = _clamp(safe_stddev / 64.0)

        # Edge density is a lightweight salience proxy that works without OpenCV.
        resized = gray.resize((min(256, width), min(256, height)))
        pixels = list(resized.getdata())
        if not pixels:
            edge_density = 0.0
        else:
            w, h = resized.size
            edges = 0
            comparisons = 0
            for y in range(h):
                for x in range(w):
                    current = pixels[y * w + x]
                    if x + 1 < w:
                        comparisons += 1
                        edges += abs(current - pixels[y * w + x + 1]) >= 24
                    if y + 1 < h:
                        comparisons += 1
                        edges += abs(current - pixels[(y + 1) * w + x]) >= 24
            edge_density = _clamp((edges / max(1, comparisons)) * 5.0)

        # Penalize extremely small/flat images and reward healthy dynamic range.
        size_ok = width >= 640 and height >= 360
        score = (
            0.35 * contrast
            + 0.30 * text_contrast
            + 0.25 * edge_density
            + 0.10 * float(size_ok)
        )
        return {
            "ok": bool(size_ok and score >= 0.45),
            "score": round(_clamp(score), 4),
            "contrast": round(contrast, 4),
            "text_contrast": round(text_contrast, 4),
            "edge_density": round(edge_density, 4),
            "width": float(width),
            "height": float(height),
        }


def rank_variants(paths: Iterable[str | Path]) -> list[tuple[str, dict[str, float | bool]]]:
    scored = [(str(path), score_thumbnail(path)) for path in paths]
    return sorted(scored, key=lambda item: float(item[1]["score"]), reverse=True)


__all__ = ["rank_variants", "score_thumbnail"]
