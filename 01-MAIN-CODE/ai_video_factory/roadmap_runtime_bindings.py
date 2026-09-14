"""Bind roadmap runtime contracts into modules that imported symbols early."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


def install() -> None:
    from . import complete_factory, content_factory, production_pipeline

    complete_factory.adaptive_pacing = content_factory.adaptive_pacing
    complete_factory.choose_clip_count = content_factory.choose_clip_count
    complete_factory.detect_emotion = content_factory.detect_emotion
    complete_factory.validate_render = content_factory.validate_render

    original_score_shots = content_factory.score_shots

    def score_shots(shots: Sequence[Mapping[str, Any]], topic: str = "") -> list[Dict[str, Any]]:
        ranked = original_score_shots(shots, topic)
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            model = SentenceTransformer("all-MiniLM-L6-v2")
            texts = [str(topic)] + [str(item.get("text") or item.get("description") or item.get("transcript") or "") for item in shots]
            vectors = model.encode(texts, normalize_embeddings=True)
            topic_vector = vectors[0]
            for row, vector in zip(ranked, vectors[1:]):
                semantic = float(sum(a * b for a, b in zip(topic_vector, vector)))
                row["semantic_similarity"] = round(max(0.0, min(1.0, semantic)), 5)
                row["score"] = round(
                    float(row.get("motion", 0.0)) * 0.20
                    + float(row.get("faces", 0.0)) * 0.10
                    + float(row.get("brightness", 0.0)) * 0.10
                    + float(row.get("uniqueness", 0.0)) * 0.20
                    + float(row.get("audio_relevance", 0.0)) * 0.15
                    + row["semantic_similarity"] * 0.25,
                    5,
                )
            ranked.sort(key=lambda item: (-item["score"], str(item.get("shot_id", ""))))
            return ranked
        except Exception:
            return ranked

    content_factory.score_shots = score_shots
    complete_factory.score_shots = score_shots

    # Make the main footage-aware production path honor the selected output
    # profile instead of silently using a fixed 9:16 timeline.
    original_run = complete_factory.run_production_pipeline
    original_build_timeline = production_pipeline.build_timeline
    aspect_by_platform = {
        "youtube_shorts": "9:16",
        "tiktok": "9:16",
        "instagram_reels": "9:16",
        "youtube": "16:9",
        "square": "1:1",
    }

    def build_timeline_for_platform(*args: Any, **kwargs: Any) -> Any:
        aspect = kwargs.pop("aspect_ratio", None)
        selected = getattr(production_pipeline, "_aivf_active_aspect", None)
        kwargs["aspect_ratio"] = selected or aspect or "9:16"
        return original_build_timeline(*args, **kwargs)

    production_pipeline.build_timeline = build_timeline_for_platform

    def run_with_platform_timeline(*args: Any, **kwargs: Any) -> Any:
        platform = str(kwargs.get("platform", "youtube_shorts"))
        production_pipeline._aivf_active_aspect = aspect_by_platform.get(platform, "9:16")
        try:
            return original_run(*args, **kwargs)
        finally:
            production_pipeline._aivf_active_aspect = None

    complete_factory.run_production_pipeline = run_with_platform_timeline

    # Ensure competitor research uses real public web/video signals when
    # available instead of only comparing against caller-supplied rows.
    def competitor_gap(topic: str, *, candidates: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
        if candidates:
            return content_factory.competitor_content_gap(topic, candidates=candidates)
        try:
            from .web_browser import deep_research
            web = deep_research(topic, max_search=10, max_scrape=5)
            videos = web.get("videos", []) if isinstance(web, dict) else []
            rows = []
            for item in videos:
                if isinstance(item, Mapping):
                    rows.append({"title": item.get("title") or item.get("name") or "", "url": item.get("url") or item.get("link") or ""})
            if rows:
                gaps = content_factory.competitor_content_gap(topic, candidates=rows)
                gaps.update({"source": "deep_research", "videos": rows[:20]})
                return gaps
        except Exception as exc:
            return {"topic": topic, "available": False, "gaps": [], "warning": str(exc), "source": "deep_research"}
        return content_factory.competitor_content_gap(topic)

    complete_factory.competitor_content_gap = competitor_gap

    def trend(topic: str, *, limit: int = 10) -> Dict[str, Any]:
        result = content_factory.trend_research(topic, limit=limit)
        try:
            from .web_browser import deep_research
            web = deep_research(topic, max_search=min(10, limit), max_scrape=min(5, max(1, limit // 2)))
            if isinstance(web, dict) and web.get("summary_text"):
                result["deep_research_summary"] = str(web["summary_text"])[:4000]
            if isinstance(web, dict) and web.get("videos"):
                result["video_signals"] = web["videos"][:limit]
        except Exception:
            pass
        return result

    complete_factory.trend_research = trend

    # Extend channel learning with optional music, thumbnail and tone evidence.
    original_learn = content_factory.learn_channel_style

    def learn_channel_style(videos: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        profile = original_learn(videos)
        if not videos:
            return profile
        def mode(key: str, default: str) -> str:
            values = [str(v.get(key)) for v in videos if v.get(key) not in (None, "")]
            return max(set(values), key=values.count) if values else default
        profile.setdefault("profile", {}).update({
            "music_style": mode("music_style", "adaptive"),
            "thumbnail_style": mode("thumbnail_style", "learned_variants"),
            "tone": mode("tone", "direct"),
        })
        return profile

    content_factory.learn_channel_style = learn_channel_style
    complete_factory.learn_channel_style = learn_channel_style

    # Thumbnail ranking is based on actual image pixels rather than equal
    # placeholder scores.
    def ranked_thumbnail(package_dir: str, variants: Sequence[str], topic: str) -> Dict[str, Any]:
        rows = []
        for index, path in enumerate(variants, start=1):
            face_salience = 0.0
            contrast = 0.0
            clarity = 0.0
            try:
                from PIL import Image, ImageFilter, ImageStat
                image = Image.open(path).convert("RGB")
                stat = ImageStat.Stat(image)
                mean = sum(stat.mean) / 3.0
                rms = sum(stat.rms) / 3.0
                contrast = min(1.0, max(0.0, (rms - mean) / 64.0))
                edges = image.filter(ImageFilter.FIND_EDGES)
                edge_stat = ImageStat.Stat(edges)
                clarity = min(1.0, sum(edge_stat.mean) / 3.0 / 40.0)
                try:
                    import cv2  # type: ignore
                    import numpy as np  # type: ignore
                    gray = np.array(image.convert("L"))
                    faces = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml").detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
                    face_salience = min(1.0, len(faces) / 3.0)
                except Exception:
                    pass
            except Exception:
                pass
            rows.append({"id": str(index), "path": path, "text": topic, "face_salience": face_salience, "contrast": contrast, "clarity": clarity})
        ranked = content_factory.thumbnail_factory_plan(rows)
        selected = ranked[0]["path"] if ranked else None
        target = str(Path(package_dir) / "thumbnail_ranked.png") if selected else None
        if selected and target:
            from shutil import copyfile
            copyfile(selected, target)
        return {"ranked": ranked, "selected": target}

    complete_factory._ranked_thumbnail = ranked_thumbnail
