"""Rights-aware topic-to-video rendering for the Director pipeline.

This path deliberately uses only Wikimedia Commons images whose metadata identifies
a commercially reusable CC BY (without NC/ND/SA restrictions) or public-domain
license. Unverified YouTube/Reddit assets are never downloaded into a generated video.
Automated license classification is not human publishing approval.
"""
from __future__ import annotations

import html
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .render_engine import run_ffmpeg, run_ffprobe, validate_media_output

WIDTH = 720
HEIGHT = 1280
FPS = 30
MAX_ASSET_BYTES = 32 * 1024 * 1024
ALLOWED_LICENSE_BASES = {"cc_license", "public_domain"}


def _plain_text(value: Any, limit: int = 300) -> str:
    text = html.unescape(re.sub(r"<[^>]*>", " ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _eligible_visual(item: Mapping[str, Any]) -> bool:
    """Require a source URL, known eligible license, and Commons metadata."""
    if str(item.get("source") or "").lower() != "wikimedia":
        return False
    if str(item.get("rights_status") or "").lower() != "cleared":
        return False
    if str(item.get("rights_basis") or "").lower() not in ALLOWED_LICENSE_BASES:
        return False
    parsed = urlparse(str(item.get("url") or ""))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "upload.wikimedia.org":
        return False
    license_text = str(item.get("license") or "").lower()
    if not license_text:
        return False
    if any(term in license_text for term in ("noncommercial", "non-commercial", "no derivatives", "no-derivatives", "cc by-sa", "cc-by-sa", "cc by-nc", "cc-by-nc")):
        return False
    if str(item.get("rights_basis")).lower() == "cc_license" and "cc by" not in license_text:
        return False
    if str(item.get("rights_basis")).lower() == "public_domain" and not any(
        term in license_text for term in ("public domain", "cc0", "cc zero")
    ):
        return False
    return bool(item.get("license_url") or item.get("evidence_url"))


def _safe_image(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 0 or path.stat().st_size > MAX_ASSET_BYTES:
            return False
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def _chunks_for_video(script: str, count: int) -> list[str]:
    words = re.findall(r"\S+", script.strip())
    if not words:
        return []
    count = max(1, min(count, len(words)))
    result: list[str] = []
    for index in range(count):
        start = round(len(words) * index / count)
        end = round(len(words) * (index + 1) / count)
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            result.append(chunk)
    return result


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _build_slide(
    source: Path,
    destination: Path,
    *,
    topic: str,
    caption: str,
    attribution: str,
) -> None:
    with Image.open(source) as original:
        background = ImageOps.fit(
            original.convert("RGB"),
            (WIDTH, HEIGHT),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.42),
        )
    frame = background.convert("RGBA")
    shade = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    draw_shade = ImageDraw.Draw(shade)
    draw_shade.rectangle((0, 0, WIDTH, 160), fill=(4, 7, 13, 170))
    draw_shade.rectangle((0, HEIGHT - 410, WIDTH, HEIGHT), fill=(4, 7, 13, 210))
    frame = Image.alpha_composite(frame, shade)
    draw = ImageDraw.Draw(frame)
    label_font = _font(20)
    title_font = _font(26)
    caption_font = _font(37)
    credit_font = _font(14)
    draw.text((42, 28), "EDIT FACTORY  /  IDEA TO VIDEO", font=label_font, fill=(255, 161, 145, 255))
    title_text = _plain_text(topic, 72)
    title_lines = _wrap_text(draw, title_text, title_font, WIDTH - 84)[:2]
    for line_number, line in enumerate(title_lines):
        draw.text((42, 62 + 31 * line_number), line, font=title_font, fill=(255, 255, 255, 255))
    caption_lines = _wrap_text(draw, _plain_text(caption, 900), caption_font, WIDTH - 84)[:5]
    caption_y = HEIGHT - 365
    line_height = 49
    for line_number, line in enumerate(caption_lines):
        draw.text((42, caption_y + line_number * line_height), line, font=caption_font, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 150))
    credit = _plain_text(attribution, 125)
    if credit:
        draw.text((42, HEIGHT - 44), credit, font=credit_font, fill=(228, 232, 240, 255))
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.convert("RGB").save(destination, format="PNG", optimize=True)


def _duration(path: Path) -> float | None:
    try:
        result = run_ffprobe([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ], timeout=20)
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0)
        return duration if math.isfinite(duration) and duration > 0 else None
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _ffconcat_quote(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")


def _render_slides(slides: list[tuple[Path, float]], work_dir: Path, silent_video: Path) -> None:
    rendered: list[Path] = []
    for index, (slide, duration) in enumerate(slides, start=1):
        segment = work_dir / f"scene_{index:02d}.mp4"
        run_ffmpeg([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-framerate", str(FPS), "-i", str(slide),
            "-t", f"{duration:.3f}",
            "-vf", "zoompan=z='min(zoom+0.0012,1.08)':d=1:s=720x1280:fps=30,format=yuv420p",
            "-an", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-movflags", "+faststart", str(segment),
        ], timeout=180)
        if not segment.is_file() or segment.stat().st_size <= 0:
            raise RuntimeError(f"FFmpeg did not produce storyboard scene {index}")
        rendered.append(segment)

    concat_file = work_dir / "storyboard.ffconcat"
    concat_file.write_text(
        "ffconcat version 1.0\n" + "".join(f"file '{_ffconcat_quote(path)}'\n" for path in rendered),
        encoding="utf-8",
    )
    run_ffmpeg([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-c", "copy", "-movflags", "+faststart", str(silent_video),
    ], timeout=180)
    if not silent_video.is_file() or silent_video.stat().st_size <= 0:
        raise RuntimeError("FFmpeg did not produce the storyboard master")


def create_idea_video(ctx: Any) -> dict[str, Any]:
    """Create a vertical narrated/ captioned slideshow from license-filtered visuals."""
    package = Path(str(ctx.package_dir or "")).resolve()
    if not package.is_dir():
        raise ValueError("Idea-to-Video requires a valid package directory")
    topic = _plain_text(ctx.topic, 500)
    script = str(ctx.script or "").strip()
    if len(topic) < 2 or not script:
        raise ValueError("Idea-to-Video requires a topic and a non-empty script")

    asset_dir = package / "idea_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    from .visuals_fetcher import download_visuals, fetch_visuals

    candidates = fetch_visuals(topic, max_items=24)
    eligible = [dict(item) for item in candidates if isinstance(item, Mapping) and _eligible_visual(item)]
    downloaded = download_visuals(eligible, str(asset_dir), download_clips=False)
    assets: list[dict[str, Any]] = []
    for item in downloaded:
        local = Path(str(item.get("local_path") or "")).resolve()
        try:
            local.relative_to(asset_dir.resolve())
        except ValueError:
            continue
        if not _safe_image(local):
            continue
        assets.append(dict(item, local_path=str(local)))
    if not assets:
        raise RuntimeError(
            "Idea-to-Video could not find a downloadable image with an eligible Commons license. "
            "No unverified Reddit or YouTube media was used. Try a more specific topic or provide source footage in Clip Factory."
        )

    target = float(ctx.target_seconds)
    voiceover = Path(str(ctx.voiceover or "")).expanduser() if ctx.voiceover else package / "voiceover.mp3"
    if not voiceover.is_file():
        voiceover = Path("")
    voice_duration = _duration(voiceover) if str(voiceover) not in {"", "."} and voiceover.is_file() else None
    duration = min(target, max(8.0, voice_duration)) if voice_duration else target
    scene_count = max(3, min(8, math.ceil(duration / 7.0)))
    captions = _chunks_for_video(script, scene_count)
    if not captions:
        raise RuntimeError("Idea-to-Video could not make caption scenes from the script")
    scene_count = len(captions)
    scene_duration = duration / scene_count

    work_dir = package / "_idea_video_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    slides: list[tuple[Path, float]] = []
    scene_rows: list[dict[str, Any]] = []
    for index, caption in enumerate(captions):
        asset = assets[index % len(assets)]
        original_path = Path(str(asset["local_path"]))
        license_name = _plain_text(asset.get("license") or "Public domain", 100)
        artist = _plain_text(asset.get("artist") or asset.get("title") or "Wikimedia Commons", 100)
        attribution = f"{artist} · {license_name}"
        frame_path = work_dir / f"slide_{index + 1:02d}.png"
        _build_slide(original_path, frame_path, topic=topic, caption=caption, attribution=attribution)
        slides.append((frame_path, scene_duration))
        scene_rows.append({
            "scene": index + 1,
            "caption": caption,
            "duration_seconds": round(scene_duration, 3),
            "frame": str(frame_path.relative_to(package)).replace("\\", "/"),
            "asset_url": asset.get("url"),
            "asset_title": _plain_text(asset.get("title") or "", 300),
            "artist": artist,
            "license": license_name,
            "license_url": str(asset.get("license_url") or ""),
            "evidence_url": str(asset.get("evidence_url") or asset.get("url") or ""),
            "rights_basis": str(asset.get("rights_basis") or ""),
            "transformation": "cropped to 9:16 and captioned for a vertical video",
        })

    silent_video = work_dir / "storyboard_silent.mp4"
    _render_slides(slides, work_dir, silent_video)
    final_video = package / "final_idea_video.mp4"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(silent_video),
    ]
    has_voiceover = voiceover.is_file() and voice_duration is not None
    if has_voiceover:
        command.extend(["-i", str(voiceover)])
        audio_input = "1:a:0"
    else:
        command.extend(["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])
        audio_input = "1:a:0"
    command.extend([
        "-map", "0:v:0", "-map", audio_input, "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(final_video),
    ])
    run_ffmpeg(command, timeout=240)
    if not final_video.is_file() or final_video.stat().st_size <= 0:
        raise RuntimeError("FFmpeg did not produce the final Idea-to-Video artifact")
    validation = validate_media_output(str(final_video), require_video=True, require_audio=True)
    if not validation:
        raise RuntimeError("Final Idea-to-Video media validation returned no result")

    rights_manifest = {
        "schema_version": 1,
        "topic": topic,
        "asset_policy": "generation uses only Commons metadata classified as public domain or CC BY without NC/ND/SA restrictions",
        "assets": [
            {
                "asset_id": f"commons-{index + 1}",
                "source": "wikimedia_commons",
                "url": str(asset.get("url") or ""),
                "title": _plain_text(asset.get("title") or "", 300),
                "artist": _plain_text(asset.get("artist") or "", 300),
                "license": _plain_text(asset.get("license") or "", 120),
                "license_url": str(asset.get("license_url") or ""),
                "evidence_url": str(asset.get("evidence_url") or asset.get("url") or ""),
                "rights_basis": str(asset.get("rights_basis") or ""),
                "use_for_generation": "eligible_by_metadata",
                "human_review": "required_before_publishing",
                "commercial_rights_guarantee": False,
            }
            for index, asset in enumerate(assets)
        ],
        "publication": {
            "status": "review_required",
            "publish_ready": False,
            "approval_required": True,
            "reason": "Automated license metadata filtering is not human rights approval; verify attribution, license terms, claims, final media, and account eligibility before posting.",
            "verified_live_destinations": [],
        },
    }
    (package / "rights_manifest.json").write_text(
        json.dumps(rights_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (package / "storyboard.json").write_text(
        json.dumps({
            "schema_version": 1,
            "production_mode": "idea_to_video",
            "topic": topic,
            "target_seconds": target,
            "rendered_seconds": round(duration, 3),
            "script": script,
            "scenes": scene_rows,
            "final_video": final_video.name,
            "voiceover": str(voiceover) if has_voiceover else None,
            "voiceover_status": "included" if has_voiceover else "unavailable_captioned_video_with_silent_audio",
            "music_status": "omitted_until_commercial_use_license_is_verifiable",
            "quality_validation": "passed",
            "publication": rights_manifest["publication"],
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not has_voiceover:
        ctx.warnings.append("Voiceover was unavailable; generated captions with a silent audio track. Review before publishing.")
    ctx.final_video = str(final_video)
    ctx.metrics.update({
        "production_mode": "idea_to_video",
        "actual_duration_seconds": round(duration, 3),
        "storyboard_scenes": len(scene_rows),
        "licensed_visuals_used": len(assets),
        "has_voiceover": has_voiceover,
        "music_added": False,
    })
    return {
        "final_video": str(final_video),
        "asset_count": len(assets),
        "scene_count": len(scene_rows),
        "voiceover_included": has_voiceover,
        "publish_ready": False,
    }


__all__ = ["create_idea_video"]
