"""Rights-aware topic-to-video rendering for the Director pipeline.

This path deliberately uses only Wikimedia Commons images whose metadata identifies
a commercially reusable CC BY (without NC/ND/SA restrictions) or public-domain
license. Unverified YouTube/Reddit assets are never downloaded into a generated video.
Automated license classification is not human publishing approval.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse

import requests

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
    source = str(item.get("source") or "").lower()
    if source not in {"wikimedia", "wikimedia_video"}:
        return False
    if str(item.get("rights_status") or "").lower() != "cleared":
        return False
    if str(item.get("rights_basis") or "").lower() not in ALLOWED_LICENSE_BASES:
        return False
    parsed = urlparse(str(item.get("url") or ""))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "upload.wikimedia.org":
        return False
    suffix = Path(parsed.path).suffix.lower()
    if source == "wikimedia_video":
        if not str(item.get("mime") or "").lower().startswith("video/"):
            return False
        if suffix not in {".webm", ".mp4", ".ogv", ".ogg", ".mov"}:
            return False
    elif suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"}:
        return False
    license_text = str(item.get("license") or "").lower()
    if not license_text:
        return False
    if any(term in license_text for term in (
        "noncommercial", "non-commercial", "no derivatives", "no-derivatives",
        "cc by-sa", "cc-by-sa", "cc by-nc", "cc-by-nc", "cc by-nd", "cc-by-nd",
    )):
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


def _srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, remainder = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{remainder:03d}"


def _write_scene_captions(package: Path, scene_rows: list[dict[str, Any]]) -> Path:
    path = package / "captions.srt"
    rows: list[str] = []
    cursor = 0.0
    for number, scene in enumerate(scene_rows, start=1):
        length = max(0.1, float(scene.get("duration_seconds") or 0.0))
        start = cursor
        end = cursor + length
        rows.append(
            f"{number}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n"
            f"{_plain_text(scene.get('caption') or '', 900)}\n"
        )
        cursor = end
    path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return path


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


def _build_video_overlay(destination: Path, *, topic: str, caption: str, attribution: str) -> None:
    frame = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(frame)
    label_font = _font(20)
    title_font = _font(26)
    caption_font = _font(37)
    credit_font = _font(14)
    draw.rectangle((0, 0, WIDTH, 160), fill=(4, 7, 13, 185))
    draw.rectangle((0, HEIGHT - 410, WIDTH, HEIGHT), fill=(4, 7, 13, 210))
    draw.text((42, 28), "EDIT FACTORY  /  IDEA TO VIDEO", font=label_font, fill=(255, 161, 145, 255))
    for number, line in enumerate(_wrap_text(draw, _plain_text(topic, 72), title_font, WIDTH - 84)[:2]):
        draw.text((42, 62 + 31 * number), line, font=title_font, fill=(255, 255, 255, 255))
    for number, line in enumerate(_wrap_text(draw, _plain_text(caption, 900), caption_font, WIDTH - 84)[:5]):
        draw.text((42, HEIGHT - 365 + number * 49), line, font=caption_font, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 150))
    credit = _plain_text(attribution, 125)
    if credit:
        draw.text((42, HEIGHT - 44), credit, font=credit_font, fill=(238, 241, 248, 255))
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.save(destination, format="PNG", optimize=True)


def _render_slides(
    slides: list[tuple[Path, float, Path | None, bool, float]],
    work_dir: Path,
    silent_video: Path,
) -> None:
    rendered: list[Path] = []
    for index, (slide, duration, overlay, is_video, start_offset) in enumerate(slides, start=1):
        segment = work_dir / f"scene_{index:02d}.mp4"
        if is_video and overlay is not None:
            run_ffmpeg([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-stream_loop", "-1", "-ss", f"{max(0.0, start_offset):.3f}", "-i", str(slide),
                "-loop", "1", "-framerate", str(FPS), "-i", str(overlay),
                "-t", f"{duration:.3f}",
                "-filter_complex",
                "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
                "zoompan=z='min(zoom+0.0006,1.05)':d=1:s=720x1280:fps=30,format=yuv420p[bg];"
                "[1:v]format=rgba[caption];[bg][caption]overlay=0:0:shortest=1,format=yuv420p[outv]",
                "-map", "[outv]", "-an", "-r", str(FPS), "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart",
                str(segment),
            ], timeout=180)
        else:
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


def _licensed_music_candidates(limit: int = 8) -> list[dict[str, Any]]:
    """Search Commons for audio with metadata that can plausibly support commercial use."""
    results: list[dict[str, Any]] = []
    try:
        response = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "generator": "search",
                "gsrsearch": "instrumental background music",
                "gsrnamespace": 6,
                "gsrlimit": max(1, min(int(limit), 12)),
                "prop": "imageinfo|info",
                "iiprop": "url|extmetadata|mime|size",
            },
            timeout=8,
            headers={"User-Agent": "ai-video-factory/1.0"},
        )
        if not response.ok:
            return results
        pages = response.json().get("query", {}).get("pages", {})
        for page in pages.values():
            rows = page.get("imageinfo") or []
            if not rows:
                continue
            info = rows[0]
            mime = str(info.get("mime") or "").lower()
            url = str(info.get("url") or "")
            parsed = urlparse(url)
            suffix = Path(parsed.path).suffix.lower()
            if not mime.startswith("audio/") or suffix not in {
                ".mp3", ".ogg", ".oga", ".wav", ".flac", ".opus", ".aac", ".m4a"
            }:
                continue
            if parsed.scheme != "https" or (parsed.hostname or "").lower() != "upload.wikimedia.org":
                continue
            metadata = info.get("extmetadata") or {}
            license_name = _plain_text((metadata.get("LicenseShortName") or {}).get("value") or "", 120)
            license_url = _plain_text((metadata.get("LicenseUrl") or {}).get("value") or "", 500)
            lower = license_name.lower()
            cc_by = "cc by" in lower and not any(
                token in lower for token in (
                    "nc", "nd", "sa", "noncommercial", "non-commercial",
                    "no derivatives", "no-derivatives",
                )
            )
            public_domain = "public domain" in lower or lower in {"cc0", "cc zero"}
            if not (cc_by or public_domain) or not license_url:
                continue
            size = info.get("size")
            try:
                if size is not None and int(size) > 32 * 1024 * 1024:
                    continue
            except (TypeError, ValueError, OverflowError):
                continue
            title = _plain_text(page.get("title") or "", 300)
            source_page = (
                "https://commons.wikimedia.org/wiki/"
                + quote(title.replace(" ", "_"), safe=":/()")
            )
            results.append({
                "url": url,
                "title": title,
                "mime": mime,
                "size_bytes": size,
                "license": license_name,
                "license_url": license_url,
                "evidence_url": license_url or source_page,
                "artist": _plain_text((metadata.get("Artist") or {}).get("value") or "", 300),
                "attribution_required": bool(cc_by),
                "rights_basis": "cc_license" if cc_by else "public_domain",
                "rights_status": "cleared",
                "declared_by": "wikimedia-commons-metadata",
                "declared_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            })
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return results
    return results


def _download_licensed_music(asset_dir: Path) -> dict[str, Any] | None:
    """Download and validate one small Commons audio track, otherwise return None."""
    from .visuals_fetcher import _download_http_bounded

    asset_dir.mkdir(parents=True, exist_ok=True)
    for item in _licensed_music_candidates():
        url = str(item.get("url") or "")
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in {".mp3", ".ogg", ".oga", ".wav", ".flac", ".opus", ".aac", ".m4a"}:
            continue
        destination = asset_dir / ("music_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] + suffix)
        if not _download_http_bounded(url, str(destination), max_bytes=32 * 1024 * 1024):
            continue
        try:
            media = validate_media_output(str(destination), require_video=False, require_audio=True)
            duration = float((media.get("format") or {}).get("duration") or 0.0)
            if not math.isfinite(duration) or duration <= 0:
                raise RuntimeError("Commons music asset has an invalid duration")
            return {**item, "local_path": str(destination), "duration_seconds": duration}
        except (OSError, RuntimeError, TypeError, ValueError):
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
    return None


def create_idea_video(ctx: Any) -> dict[str, Any]:
    """Create a vertical topic video from license-filtered visuals, narration, and optional cleared music."""
    package = Path(str(ctx.package_dir or "")).resolve()
    if not package.is_dir():
        raise ValueError("Idea-to-Video requires a valid package directory")
    topic = _plain_text(ctx.topic, 500)
    script = str(ctx.script or "").strip()
    if len(topic) < 2 or not script:
        raise ValueError("Idea-to-Video requires a topic and a non-empty script")

    asset_dir = package / "idea_assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    from .visuals_fetcher import download_visuals, download_wikimedia_video, fetch_visuals

    candidates = fetch_visuals(topic, max_items=24)
    eligible = [dict(item) for item in candidates if isinstance(item, Mapping) and _eligible_visual(item)]
    eligible_videos = [item for item in eligible if item.get("source") == "wikimedia_video"]
    eligible_images = [item for item in eligible if item.get("source") == "wikimedia"]
    assets: list[dict[str, Any]] = []
    # Prefer moving footage, but bound the number of remote downloads and bytes.
    for candidate in eligible_videos[:3]:
        try:
            announced_size = int(candidate.get("size_bytes") or 0)
        except (TypeError, ValueError, OverflowError):
            announced_size = 0
        if announced_size > 128 * 1024 * 1024:
            continue
        downloaded_video = download_wikimedia_video(candidate, str(asset_dir), max_bytes=128 * 1024 * 1024)
        local = Path(str(downloaded_video.get("local_path") or "")).resolve() if downloaded_video else Path(".")
        try:
            local.relative_to(asset_dir.resolve())
        except ValueError:
            continue
        if downloaded_video and local.is_file() and local.stat().st_size > 0:
            assets.append(dict(downloaded_video, local_path=str(local), media_type="video"))
        if len(assets) >= 3:
            break

    # Prefer up to three distinct source assets. If only one or two moving
    # clips are eligible, use a small still-image fallback for visual variety.
    image_fallback_count = max(0, 3 - len(assets))
    downloaded = (
        download_visuals(eligible_images[:image_fallback_count], str(asset_dir), download_clips=False)
        if image_fallback_count else []
    )
    for item in downloaded:
        local = Path(str(item.get("local_path") or "")).resolve()
        try:
            local.relative_to(asset_dir.resolve())
        except ValueError:
            continue
        if _safe_image(local):
            assets.append(dict(item, local_path=str(local), media_type="image"))
    if not assets:
        raise RuntimeError(
            "Idea-to-Video could not find a downloadable Commons video or image with an eligible license. "
            "No unverified Reddit or YouTube media was used. Try a more specific topic or provide source footage in Clip Factory."
        )

    target = float(ctx.target_seconds)
    if ctx.voiceover:
        voiceover = Path(str(ctx.voiceover)).expanduser()
        if not voiceover.is_absolute():
            voiceover = package / voiceover
    else:
        voiceover = package / "voiceover.mp3"
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
    slides: list[tuple[Path, float, Path | None, bool, float]] = []
    scene_rows: list[dict[str, Any]] = []
    for index, caption in enumerate(captions):
        asset = assets[index % len(assets)]
        original_path = Path(str(asset["local_path"]))
        license_name = _plain_text(asset.get("license") or "Public domain", 100)
        artist = _plain_text(asset.get("artist") or asset.get("title") or "Wikimedia Commons", 100)
        attribution = f"{artist} · {license_name}"
        if asset.get("media_type") == "video":
            overlay_path = work_dir / f"overlay_{index + 1:02d}.png"
            _build_video_overlay(overlay_path, topic=topic, caption=caption, attribution=attribution)
            try:
                source_duration = max(0.0, float(asset.get("duration_seconds") or 0.0))
            except (TypeError, ValueError, OverflowError):
                source_duration = 0.0
            maximum_start = max(0.0, source_duration - scene_duration)
            start_offset = (index * scene_duration) % maximum_start if maximum_start > 0 else 0.0
            slides.append((original_path, scene_duration, overlay_path, True, start_offset))
            frame_name = str(overlay_path.relative_to(package)).replace("\\", "/")
        else:
            frame_path = work_dir / f"slide_{index + 1:02d}.png"
            _build_slide(original_path, frame_path, topic=topic, caption=caption, attribution=attribution)
            slides.append((frame_path, scene_duration, None, False, 0.0))
            frame_name = str(frame_path.relative_to(package)).replace("\\", "/")
        scene_rows.append({
            "scene": index + 1,
            "caption": caption,
            "duration_seconds": round(scene_duration, 3),
            "frame_or_overlay": frame_name,
            "asset_type": str(asset.get("media_type") or "image"),
            "asset_url": asset.get("url"),
            "asset_title": _plain_text(asset.get("title") or "", 300),
            "artist": artist,
            "license": license_name,
            "license_url": str(asset.get("license_url") or ""),
            "evidence_url": str(asset.get("evidence_url") or asset.get("url") or ""),
            "rights_basis": str(asset.get("rights_basis") or ""),
            "transformation": "source video excerpt or image cropped to 9:16 and captioned",
        })

    used_assets = assets[:min(len(assets), len(captions))]
    silent_video = work_dir / "storyboard_silent.mp4"
    _render_slides(slides, work_dir, silent_video)
    final_video = package / "final_idea_video.mp4"
    has_voiceover = voiceover.is_file() and voice_duration is not None
    music = _download_licensed_music(asset_dir)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(silent_video),
    ]
    next_input = 1
    voice_index: int | None = None
    music_index: int | None = None
    if has_voiceover:
        voice_index = next_input
        command.extend(["-i", str(voiceover)])
        next_input += 1
    if music:
        music_index = next_input
        command.extend(["-stream_loop", "-1", "-i", str(music["local_path"])])
        next_input += 1

    if voice_index is not None and music_index is not None:
        command.extend([
            "-filter_complex",
            f"[{voice_index}:a]volume=1.0[voice];"
            f"[{music_index}:a]volume=0.12[music];"
            "[voice][music]amix=inputs=2:duration=longest:dropout_transition=2,"
            "alimiter=limit=0.95[aout]",
            "-map", "0:v:0", "-map", "[aout]",
        ])
    elif voice_index is not None:
        command.extend(["-map", "0:v:0", "-map", f"{voice_index}:a:0", "-af", "apad"])
    elif music_index is not None:
        command.extend(["-map", "0:v:0", "-map", f"{music_index}:a:0"])
    else:
        command.extend([
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map", "0:v:0", "-map", f"{next_input}:a:0",
        ])
    command.extend([
        "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
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
                "asset_type": str(asset.get("media_type") or ("video" if asset.get("source") == "wikimedia_video" else "image")),
                "url": str(asset.get("url") or ""),
                "title": _plain_text(asset.get("title") or "", 300),
                "artist": _plain_text(asset.get("artist") or "", 300),
                "license": _plain_text(asset.get("license") or "", 120),
                "rights_status": str(asset.get("rights_status") or "cleared"),
                "attribution_required": bool(asset.get("attribution_required", str(asset.get("rights_basis") or "") == "cc_license")),
                "declared_by": str(asset.get("declared_by") or "wikimedia-commons-metadata"),
                "declared_at": str(asset.get("declared_at") or ""),
                "license_url": str(asset.get("license_url") or ""),
                "evidence_url": str(asset.get("evidence_url") or asset.get("url") or ""),
                "rights_basis": str(asset.get("rights_basis") or ""),
                "use_for_generation": "eligible_by_metadata",
                "human_review": "required_before_publishing",
                "commercial_rights_guarantee": False,
            }
            for index, asset in enumerate(used_assets)
        ],
        "audio_assets": [
            {
                "asset_id": "commons-background-music",
                "source": "wikimedia_commons",
                "url": str(music.get("url") or ""),
                "title": _plain_text(music.get("title") or "", 300),
                "artist": _plain_text(music.get("artist") or "", 300),
                "license": _plain_text(music.get("license") or "", 120),
                "rights_status": str(music.get("rights_status") or "cleared"),
                "attribution_required": bool(music.get("attribution_required", str(music.get("rights_basis") or "") == "cc_license")),
                "declared_by": str(music.get("declared_by") or "wikimedia-commons-metadata"),
                "declared_at": str(music.get("declared_at") or ""),
                "license_url": str(music.get("license_url") or ""),
                "evidence_url": str(music.get("evidence_url") or music.get("url") or ""),
                "rights_basis": str(music.get("rights_basis") or ""),
                "human_review": "required_before_publishing",
                "commercial_rights_guarantee": False,
            }
        ] if music else [],
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
    captions_path = _write_scene_captions(package, scene_rows)
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
            "captions_srt": captions_path.name,
            "music_status": "included_from_eligible_Commons_audio" if music else "no_eligible_Commons_music_found",
            "music_asset": {
                "url": music.get("url"),
                "license": music.get("license"),
                "license_url": music.get("license_url"),
                "evidence_url": music.get("evidence_url"),
            } if music else None,
            "quality_validation": "passed",
            "publication": rights_manifest["publication"],
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if not has_voiceover:
        ctx.warnings.append("Voiceover was unavailable; generated captions with a silent or music-only audio bed. Review before publishing.")
    if not music:
        ctx.warnings.append("No eligible Commons music track was found; the final video was rendered without background music.")
    else:
        ctx.music_track = str(music["local_path"])
    ctx.final_video = str(final_video)
    ctx.metrics.update({
        "production_mode": "idea_to_video",
        "actual_duration_seconds": round(duration, 3),
        "storyboard_scenes": len(scene_rows),
        "licensed_visuals_used": len(used_assets),
        "has_voiceover": has_voiceover,
        "music_added": bool(music),
    })
    return {
        "final_video": str(final_video),
        "asset_count": len(used_assets),
        "scene_count": len(scene_rows),
        "captions_srt": str(captions_path),
        "voiceover_included": has_voiceover,
        "music_included": bool(music),
        "publish_ready": False,
    }


def choose_surprise_topic(content_profile: str = "general") -> str:
    """Pick a safe evergreen idea when the creator asks the system to choose."""
    import secrets

    normalized = str(content_profile or "general").strip().lower()
    gaming_topics = [
        "Why clutch moments feel so intense in competitive games",
        "How game sound design changes player decisions",
        "The psychology behind comeback victories in games",
        "Why limited resources make survival games tense",
        "How game maps teach players without words",
        "Why teamwork can fail even when every player is skilled",
        "What makes a fair challenge feel almost impossible",
    ]
    general_topics = [
        "Why we get goosebumps",
        "How noise-cancelling headphones actually work",
        "Why the Moon can look larger near the horizon",
        "The surprising science of why popcorn pops",
        "How octopuses solve problems",
        "Why your brain remembers some songs",
        "How bees communicate through movement",
        "Why airplane windows have rounded corners",
        "What science knows about why we dream",
        "Why time can seem to move faster as we get older",
    ]
    topics = gaming_topics if normalized == "gaming" else general_topics
    return secrets.choice(topics)


__all__ = ["create_idea_video", "choose_surprise_topic"]
