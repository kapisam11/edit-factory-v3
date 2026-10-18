"""Executable end-to-end content-factory orchestration."""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .content_factory import (
    CheckpointStore, adaptive_pacing, artifact_dependency_graph, choose_clip_count,
    clean_filler_words, clean_voiceover_words, competitor_content_gap,
    dependency_fingerprint, detect_dead_time, detect_emotion, detect_visual_redundancy,
    deterministic_manifest, duplicate_fingerprint, generate_chapters,
    generate_content_strategy, generate_hook_candidates, learn_channel_style,
    plan_source_aware_crop, provider_retry, score_shots, thumbnail_factory_plan,
    trend_research, validate_render,
)
from .artifact_readiness import evaluate_artifact
from .learning_recommender import load_experiments
from .production_models import ProductionResult
from .production_pipeline import run_production_pipeline
from .render_engine import run_ffmpeg, run_ffprobe

PLATFORM_LAYOUTS = {
    "youtube_shorts": (1080, 1920), "tiktok": (1080, 1920),
    "instagram_reels": (1080, 1920), "youtube": (1920, 1080), "square": (1080, 1080),
}


def _write_json(path: str, payload: Any) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def _run(cmd: Sequence[str]) -> None:
    try:
        run_ffmpeg(list(cmd), capture_output=True)
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _probe(path: str) -> Dict[str, Any]:
    if not shutil.which("ffprobe"):
        return {"available": False, "warning": "ffprobe unavailable"}
    try:
        result = run_ffprobe([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-show_entries", "stream=index,codec_type,codec_name,width,height,duration",
            "-of", "json", path,
        ], timeout=20)
    except Exception as exc:
        return {"available": False, "warning": str(exc)}
    if result.returncode != 0:
        return {"available": False, "warning": (result.stderr or "ffprobe failed").strip()}
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {"available": False, "warning": "ffprobe returned invalid JSON"}


def _validate_output(path: str, *, width: Optional[int] = None, height: Optional[int] = None) -> Dict[str, Any]:
    result = validate_render(path, expected_width=width, expected_height=height, min_duration=0.25)
    return {**result, "probe": result.get("format") or {}, "streams": [result.get("video"), result.get("audio")]}


def _crop_offsets(crop: Optional[Mapping[str, Any]]) -> tuple[int, int]:
    if not crop:
        return 0, 0
    nested = crop.get("crop") if isinstance(crop.get("crop"), Mapping) else crop
    return int(float(nested.get("left", nested.get("x", 0)) or 0)), int(float(nested.get("top", nested.get("y", 0)) or 0))


def _render_reframe(source: str, output: str, width: int, height: int, *, crop_plan: Optional[Mapping[str, Any]] = None) -> str:
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg is required for rendering")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
    if crop_plan:
        left, top = _crop_offsets(crop_plan)
        vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}:{left}:{top}"
    _run(["ffmpeg", "-y", "-i", source, "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-movflags", "+faststart", output])
    return output


def render_all_platforms(source: str, package_dir: str, platforms: Sequence[str], *, crop_plan: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    rendered: Dict[str, str] = {}
    for platform in platforms:
        width, height = PLATFORM_LAYOUTS[platform]
        out = os.path.join(package_dir, "renders", f"{platform}.mp4")
        _render_reframe(source, out, width, height, crop_plan=None if platform == "youtube" else crop_plan)
        check = _validate_output(out, width=width, height=height)
        if not check["ok"]:
            raise RuntimeError(f"Render validation failed for {platform}: {check['errors']}")
        rendered[platform] = out
    return rendered


def render_long_form_master(source: str, package_dir: str) -> str:
    out = os.path.join(package_dir, "long_form", "long_form_master.mp4")
    _render_reframe(source, out, 1920, 1080)
    check = _validate_output(out, width=1920, height=1080)
    if not check["ok"]:
        raise RuntimeError(f"Long-form validation failed: {check['errors']}")
    return out


def _resolve_clip_source(input_video: Optional[str], primary_video: str) -> str:
    """Use the original source for repurposing; never silently substitute a rendered short."""
    if input_video:
        original = Path(input_video).expanduser()
        if not original.is_file():
            raise FileNotFoundError(
                "The original input video is unavailable for clip extraction; "
                "refusing to generate candidates from the already-rendered short."
            )
        return str(original.resolve())

    primary = Path(primary_video).expanduser()
    if not primary.is_file():
        raise FileNotFoundError("Neither an original source video nor a primary rendered video is available")
    return str(primary.resolve())


def _clip_window_candidates(
    scenes: Sequence[Mapping[str, Any]],
    scored: Sequence[Mapping[str, Any]],
    count: int,
    duration: float,
) -> list[tuple[float, float]]:
    """Select high-scoring, source-bounded, temporally diverse clip windows.

    Scores are explainable scene heuristics, not predicted views or retention.
    Candidates are centered around their selected scene wherever source bounds allow.
    """
    try:
        source_duration = float(duration)
        requested_count = max(0, min(20, int(count)))
    except (TypeError, ValueError, OverflowError):
        return []
    if not math.isfinite(source_duration) or source_duration <= 0.5 or requested_count == 0:
        return []

    by_id = {
        str(scene.get("id", index)): scene
        for index, scene in enumerate(scenes)
        if isinstance(scene, Mapping)
    }
    clip_length = min(
        source_duration,
        min(35.0, max(min(12.0, source_duration), source_duration / max(1, min(requested_count, 6)))),
    )
    candidates: list[tuple[float, float]] = []

    for item in scored:
        if not isinstance(item, Mapping):
            continue
        scene = by_id.get(str(item.get("shot_id", "")))
        if scene is None:
            continue
        try:
            scene_start = float(scene.get("start", scene.get("start_time", scene.get("timestamp", 0.0))) or 0.0)
            scene_end = float(scene.get("end", scene.get("end_time", scene_start)) or scene_start)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(scene_start) or not math.isfinite(scene_end) or scene_end <= scene_start:
            continue

        center = max(0.0, min(source_duration, (scene_start + scene_end) / 2.0))
        start = max(0.0, min(source_duration - clip_length, center - clip_length / 2.0))
        end = start + clip_length

        # Avoid selecting several near-identical clips from adjacent high-scoring
        # scenes. The comparison uses overlap divided by the shorter clip length.
        redundant = False
        for prior_start, prior_length in candidates:
            prior_end = prior_start + prior_length
            overlap = max(0.0, min(end, prior_end) - max(start, prior_start))
            if overlap / max(0.001, min(clip_length, prior_length)) > 0.55:
                redundant = True
                break
        if redundant:
            continue

        candidates.append((round(start, 3), round(clip_length, 3)))
        if len(candidates) >= requested_count:
            break

    return candidates
def render_clip_factory(
    source: str,
    package_dir: str,
    *,
    count: int,
    crop_plan: Optional[Mapping[str, Any]] = None,
    windows: Optional[Sequence[tuple[float, float]]] = None,
) -> list[str]:
    """Render source clips and persist timestamps/method so candidates are reviewable."""
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg is required for the clip factory")
    probe = _probe(source)
    try:
        duration = float((probe.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError, OverflowError):
        duration = 0.0
    if not math.isfinite(duration) or duration <= 0.5:
        return []

    requested_count = max(1, min(20, int(count)))
    starts: list[tuple[float, float]] = []
    selection_method = "scene-ranked-diverse"

    if windows:
        seen = set()
        for raw_start, raw_length in windows[:requested_count]:
            try:
                start = float(raw_start)
                length = float(raw_length)
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(start) or not math.isfinite(length) or length <= 0:
                continue
            start = max(0.0, min(duration - 0.25, start))
            length = min(length, duration - start)
            if length < 0.5:
                continue
            key = (round(start, 3), round(length, 3))
            if key in seen:
                continue
            seen.add(key)
            starts.append(key)

    if not starts:
        selection_method = "evenly-spaced-fallback"
        clip_len = min(
            duration,
            min(35.0, max(min(12.0, duration), duration / max(1, min(requested_count, 6)))),
        )
        max_start = max(0.0, duration - clip_len)
        for index in range(requested_count):
            start = round(max_start * index / max(1, requested_count - 1), 3)
            key = (start, round(min(clip_len, duration - start), 3))
            if key[1] >= 0.5 and key not in starts:
                starts.append(key)

    outputs: list[str] = []
    candidate_rows: list[Dict[str, Any]] = []
    for index, (start, length) in enumerate(starts, start=1):
        out = os.path.join(package_dir, "clip_factory", f"short_{index:02d}.mp4")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        if crop_plan:
            left, top = _crop_offsets(crop_plan)
            vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:{left}:{top}"
        actual_length = min(length, max(0.0, duration - start))
        _run([
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", source,
            "-t", f"{actual_length:.3f}", "-vf", vf, "-c:v", "libx264",
            "-preset", "medium", "-crf", "20", "-c:a", "aac",
            "-movflags", "+faststart", out,
        ])
        check = _validate_output(out, width=1080, height=1920)
        if not check["ok"]:
            raise RuntimeError(f"Clip {index} validation failed: {check['errors']}")
        outputs.append(out)
        candidate_rows.append({
            "rank": index,
            "file": os.path.relpath(out, package_dir).replace("\\", "/"),
            "source_start_seconds": round(start, 3),
            "source_end_seconds": round(start + actual_length, 3),
            "duration_seconds": round(actual_length, 3),
            "selection_method": selection_method,
            "selection_score_semantics": "not_calibrated_to_views_or_retention",
            "reason": (
                "Ranked by source-scene evidence with temporal-diversity filtering."
                if selection_method == "scene-ranked-diverse"
                else "Fallback candidate placed at a deterministic source position because no valid scene windows were available."
            ),
        })

    _write_json(os.path.join(package_dir, "clip_candidates.json"), {
        "schema_version": 1,
        "source_video": os.path.abspath(source),
        "source_duration_seconds": round(duration, 3),
        "selection_method": selection_method,
        "score_semantics": "heuristic_scene_score_not_predicted_performance",
        "candidate_count": len(candidate_rows),
        "candidates": candidate_rows,
    })
    return outputs
def update_learning_history(path: str, *, topic: str, platform: str, package_dir: str, metrics: Mapping[str, Any], performance_metrics: Optional[Mapping[str, Any]] = None, video_id: Optional[str] = None, published_at: Optional[str] = None) -> Dict[str, Any]:
    history = load_experiments(path) if os.path.exists(path) else []
    observed_metrics: Dict[str, Any] = dict(metrics)
    root = Path(package_dir)

    # Preserve actual rendered settings even when the caller only provides a
    # small set of extra experiment variables (for example, the hook duration).
    for metrics_path in (root / "metrics.json", root / "primary" / "metrics.json"):
        try:
            payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if value is not None and (
                key not in observed_metrics
                or observed_metrics.get(key) is None
                or observed_metrics.get(key) == 0
            ):
                observed_metrics[key] = value

    # The production metadata records the settings that were actually chosen.
    # Prefer them to defaults so later retraining compares the real edit profile.
    for metadata_path in (root / "metadata.json", root / "primary" / "metadata.json"):
        try:
            metadata_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(metadata_payload, dict):
            continue
        recommendation_payload = metadata_payload.get("recommendation")
        if isinstance(recommendation_payload, Mapping):
            recommended_settings = recommendation_payload.get("settings")
            if isinstance(recommended_settings, Mapping):
                for key in (
                    "cuts_per_minute", "avg_shot_duration", "hook_duration", "music_energy",
                    "caption_style", "voice", "music_style", "content_type", "edit_type",
                ):
                    if key in recommended_settings and (
                        key not in observed_metrics
                        or observed_metrics.get(key) is None
                        or observed_metrics.get(key) == 0
                    ):
                        observed_metrics[key] = recommended_settings[key]
        if metadata_payload.get("v3_edit_type") and not observed_metrics.get("edit_type"):
            observed_metrics["edit_type"] = metadata_payload["v3_edit_type"]

    try:
        target_seconds = float(observed_metrics.get("actual_duration_seconds") or observed_metrics.get("actual_timeline_seconds") or observed_metrics.get("target_seconds") or 0.0)
        segment_count = int(observed_metrics.get("actual_rendered_segments") or observed_metrics.get("segment_count") or 0)
        cuts_per_minute = float(observed_metrics.get("cuts_per_minute") or 0.0)
    except (TypeError, ValueError):
        target_seconds, segment_count, cuts_per_minute = 0.0, 0, 0.0
    if cuts_per_minute <= 0 and target_seconds > 0 and segment_count > 0:
        cuts_per_minute = segment_count / (target_seconds / 60.0)
    average_shot = observed_metrics.get("avg_shot_duration")
    try:
        average_shot = float(average_shot or 0.0)
    except (TypeError, ValueError):
        average_shot = 0.0
    if average_shot <= 0 and cuts_per_minute > 0:
        average_shot = 60.0 / cuts_per_minute

    thinking: Dict[str, Any] = {}
    for thinking_path in (root / "video_thinking.json", root / "primary" / "video_thinking.json"):
        try:
            payload = json.loads(thinking_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            nested = payload.get("plan")
            if isinstance(nested, dict):
                thinking = nested
                break

    # Persist the actual context of each outcome so recommendations can be
    # compared within the same game, format, duration, hook and commentary type.
    gaming_profile: Dict[str, Any] = {}
    for profile_path in (root / "gaming_profile.json", root / "primary" / "gaming_profile.json"):
        try:
            raw_profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(raw_profile, dict) and raw_profile.get("enabled", True):
            gaming_profile = raw_profile
            break

    blueprint_payload: Dict[str, Any] = {}
    for blueprint_path in (root / "v3_blueprint.json", root / "primary" / "v3_blueprint.json"):
        try:
            raw_blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(raw_blueprint, dict):
            blueprint_payload = raw_blueprint
            break

    game_name = str(
        observed_metrics.get("game")
        or gaming_profile.get("game_name")
        or ""
    ).strip()
    creator_angle = str(
        observed_metrics.get("commentary_style")
        or observed_metrics.get("creator_angle")
        or gaming_profile.get("creator_angle")
        or ""
    ).strip().lower()
    first_hook = ""
    hooks = blueprint_payload.get("hooks")
    if isinstance(hooks, list) and hooks and isinstance(hooks[0], Mapping):
        first_hook = str(hooks[0].get("text") or "").strip()
    if not first_hook:
        first_hook = str(observed_metrics.get("hook") or thinking.get("hook") or "").strip()

    hook_type = str(observed_metrics.get("hook_type") or "").strip().lower()
    if not hook_type:
        hook_type = {
            "clutch": "decisive_action",
            "strategy": "instructional_problem",
            "funny": "reaction_or_reversal",
            "challenge": "challenge",
            "storytelling": "turning_point",
        }.get(creator_angle, "unspecified" if not first_hook else "creator_authored")
    duration_value = (
        observed_metrics.get("actual_duration_seconds")
        or observed_metrics.get("actual_timeline_seconds")
        or observed_metrics.get("target_seconds")
        or (target_seconds if target_seconds > 0 else None)
    )
    if not duration_value:
        for media_path in (root / "final_media_metadata.json", root / "primary" / "final_media_metadata.json"):
            try:
                media_payload = json.loads(media_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(media_payload, Mapping):
                stream_value = media_payload.get("duration_seconds") or media_payload.get("duration")
                if not stream_value and isinstance(media_payload.get("format"), Mapping):
                    stream_value = media_payload["format"].get("duration")
                if stream_value:
                    duration_value = stream_value
                    break
    if not duration_value:
        clips = blueprint_payload.get("clip_plan")
        if isinstance(clips, list) and clips and isinstance(clips[-1], Mapping):
            duration_value = clips[-1].get("end")
    try:
        duration_seconds = float(duration_value) if duration_value not in (None, "") else None
    except (TypeError, ValueError, OverflowError):
        duration_seconds = None
    if duration_seconds is not None and not 1.0 <= duration_seconds <= 3600.0:
        duration_seconds = None

    content_type = str(
        observed_metrics.get("content_type")
        or ("gaming" if gaming_profile else "short_video")
    ).strip().lower()
    format_kind = (
        "unknown-duration"
        if duration_seconds is None
        else "short" if duration_seconds <= 60.0 else "long"
    )
    format_name = str(
        observed_metrics.get("format") or f"{platform}:{format_kind}"
    ).strip().lower()
    edit_emotion = str(observed_metrics.get("emotion") or thinking.get("emotion") or "")
    record: Dict[str, Any] = {
        "topic": topic,
        "platform": platform,
        "content_type": content_type,
        "format": format_name,
        "game": game_name,
        "duration_seconds": duration_seconds,
        "hook_type": hook_type,
        "commentary_style": creator_angle or str(observed_metrics.get("commentary_style") or "unspecified"),
        "hook": first_hook[:500],
        "package_dir": package_dir,
        "cuts_per_minute": cuts_per_minute,
        "avg_shot_duration": average_shot or 2.5,
        "hook_duration": observed_metrics.get("hook_duration", 2.0),
        "music_energy": observed_metrics.get("music_energy", 0.5),
        "caption_style": observed_metrics.get("caption_style", "karaoke"),
        "voice": observed_metrics.get("voice", "en-US-GuyNeural"),
        "music_style": observed_metrics.get("music_style", edit_emotion or "dramatic"),
        "edit_type": observed_metrics.get("edit_type", edit_emotion or "dramatic"),
    }
    if performance_metrics:
        from .content_factory import performance_feedback
        record.update(performance_feedback(performance_metrics))
        record.update({k: performance_metrics[k] for k in ("views", "likes", "comments", "averageViewPercentage", "shares", "saves", "reach", "total_interactions") if k in performance_metrics})
    record["performance_score"] = float(record.get("engagement_rate") or 0.0) + float(record.get("retention") or 0.0)
    if video_id:
        record["video_id"] = str(video_id).strip()
    if published_at:
        record["published_at"] = str(published_at).strip()

    # Repeated analytics imports for one video should update its existing row,
    # not multiply the same video's weight in the training history.
    existing_index = next(
        (
            index for index, existing in enumerate(history)
            if video_id and isinstance(existing, dict)
            and str(existing.get("video_id") or "") == str(video_id)
        ),
        None,
    )
    if existing_index is None:
        history.append(record)
    else:
        merged = dict(history[existing_index])
        for key, value in record.items():
            # Reimported analytics may arrive without media metadata. Preserve
            # the earlier duration rather than replacing it with unknown.
            if key == "duration_seconds" and value is None and merged.get(key) is not None:
                continue
            merged[key] = value
        history[existing_index] = merged
        record = merged
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(history, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return record

def _fact_check_review(script: str, research: Mapping[str, Any]) -> Dict[str, Any]:
    lines = [line.strip() for line in script.splitlines() if line.strip()]
    claims = [line for line in lines if any(ch.isdigit() for ch in line) or any(word in line.lower() for word in ("was", "is", "won", "lost", "first", "only", "always", "never"))]
    return {"status": "review_required" if claims else "no_obvious_factual_claims", "claims_to_verify": claims[:25], "research_available": bool(research)}


def _build_dependency_inputs(topic: str, research: Mapping[str, Any], script: str, primary_video: str, formats: Mapping[str, str], clips: Sequence[str], *, clip_source: Optional[str] = None) -> Dict[str, Any]:
    return {"topic": {"inputs": {"topic": topic}}, "research": {"inputs": {"topic": topic, "research": research}}, "script": {"inputs": {"topic": topic, "research_fingerprint": dependency_fingerprint(research)}}, "primary_video": {"inputs": {"script_fingerprint": dependency_fingerprint({"script": script}), "source": primary_video}}, "platform_renders": {"inputs": {"primary_video": primary_video, "platforms": sorted(formats)}}, "short_clips": {"inputs": {"source_video": clip_source or primary_video, "count": len(clips), "clip_files": list(clips)}}}


def _ranked_thumbnail(package_dir: str, variants: Sequence[str], topic: str) -> Dict[str, Any]:
    rows = [{"id": str(index), "path": path, "text": topic, "face_salience": 0.5, "contrast": 0.5, "clarity": 0.5} for index, path in enumerate(variants, start=1)]
    ranked = thumbnail_factory_plan(rows)
    selected = ranked[0]["path"] if ranked else None
    target = os.path.join(package_dir, "thumbnail_ranked.png") if selected else None
    if selected and target:
        shutil.copyfile(selected, target)
    return {"ranked": ranked, "selected": target}


def run_complete_factory(input_video: Optional[str], topic: str, package_dir: str, *, target_seconds: float = 45.0, platforms: Sequence[str] = tuple(PLATFORM_LAYOUTS), clip_count: Optional[int] = None, experiment_history_path: Optional[str] = None, auto_research: bool = True, publish_youtube: bool = False, youtube_options: Optional[Mapping[str, Any]] = None, model_key: Optional[str] = None, skip_qc: bool = False) -> Dict[str, Any]:
    root = Path(package_dir)
    root.mkdir(parents=True, exist_ok=True)
    learning_history_path = (
        experiment_history_path
        or os.environ.get("AIVF_LEARNING_HISTORY_PATH", "").strip()
        or None
    )
    checkpoints = CheckpointStore(str(root / "checkpoints.json"))
    if checkpoints.is_complete("complete") and (root / "complete_factory_manifest.json").exists():
        return json.loads((root / "complete_factory_manifest.json").read_text(encoding="utf-8"))

    research: Dict[str, Any]
    if checkpoints.is_complete("research") and (root / "auto_research.json").exists():
        research = json.loads((root / "auto_research.json").read_text(encoding="utf-8"))
    else:
        research = {"topic": topic}
        if auto_research:
            research["trend_research"] = provider_retry(lambda: trend_research(topic), attempts=3)
            research["competitor_gap"] = provider_retry(lambda: competitor_content_gap(topic), attempts=3)
        research["strategy"] = generate_content_strategy(topic)
        research["emotion"] = detect_emotion(topic)
        research["hooks"] = generate_hook_candidates(topic, research, count=8)
        if learning_history_path and os.path.exists(learning_history_path):
            research["learned_style"] = learn_channel_style(load_experiments(learning_history_path))
        _write_json(str(root / "auto_research.json"), research)
        checkpoints.mark("research", status="complete", artifacts=[str(root / "auto_research.json")])

    if input_video is None:
        from .pipeline import PipelineContext, build_director_pipeline
        primary_path = root / "primary"
        primary_path.mkdir(parents=True, exist_ok=True)
        primary_dir = str(primary_path)
        ctx = PipelineContext(
            topic=topic,
            package_dir=primary_dir,
            target_seconds=target_seconds,
            model_key=model_key,
            learning_history_path=learning_history_path,
        )
        ctx = provider_retry(lambda: build_director_pipeline(verbose=False).run(ctx), attempts=2)
        primary_result = ProductionResult(package_dir=primary_dir)
        primary_result.final_video = ctx.final_video
        primary_result.script_path = str(Path(primary_dir) / "script.txt") if Path(primary_dir, "script.txt").exists() else None
        primary_result.metrics_path = str(Path(primary_dir) / "metrics.json") if Path(primary_dir, "metrics.json").exists() else None
        primary_result.warnings.extend(ctx.warnings)
        primary_result.errors.extend(ctx.errors)
    else:
        primary_dir = str(root / "primary")
        primary_result = provider_retry(lambda: run_production_pipeline(input_video, topic, primary_dir, target_seconds=target_seconds, research_summary=research, model_key=model_key, skip_qc=skip_qc, experiment_history_path=learning_history_path, platform="youtube_shorts"), attempts=3)
    if primary_result.errors or not primary_result.final_video:
        return {"status": "failed", "errors": primary_result.errors or ["Primary production produced no video"], "warnings": primary_result.warnings}
    checkpoints.mark("primary", status="complete", artifacts=[primary_result.final_video])

    primary_check = _validate_output(primary_result.final_video)
    if not primary_check["ok"]:
        return {"status": "failed", "errors": [f"Primary render validation failed: {primary_check['errors']}"], "warnings": primary_result.warnings}

    script = Path(primary_result.script_path).read_text(encoding="utf-8") if primary_result.script_path and Path(primary_result.script_path).exists() else ""
    cleaned = clean_filler_words(script)
    _write_json(str(root / "script_cleanup.json"), cleaned)
    hooks = generate_hook_candidates(topic, research, count=8)
    pacing = adaptive_pacing([float(h.get("score", 0.5)) / 10.0 for h in hooks] or [0.5], total_seconds=target_seconds)
    _write_json(str(root / "pacing.json"), {"durations": pacing})
    _write_json(str(root / "fact_check_review.json"), _fact_check_review(script, research))

    scene_path = Path(primary_result.package_dir) / "scenes.json"
    scenes: list[Dict[str, Any]] = []
    if scene_path.exists():
        try:
            payload = json.loads(scene_path.read_text(encoding="utf-8"))
            scenes = payload if isinstance(payload, list) else []
        except Exception:
            scenes = []
    scored_shots = score_shots(scenes, topic)
    redundancy = detect_visual_redundancy(scenes)
    _write_json(str(root / "shot_scores.json"), scored_shots)
    _write_json(str(root / "visual_redundancy.json"), redundancy)

    word_path = Path(primary_result.package_dir) / "word_timestamps.json"
    words: list[Dict[str, Any]] = []
    if word_path.exists():
        try:
            payload = json.loads(word_path.read_text(encoding="utf-8"))
            words = payload if isinstance(payload, list) else []
        except Exception:
            words = []
    cleaned_words = clean_voiceover_words(words)
    dead_time = detect_dead_time(words, scenes) if words else []
    _write_json(str(root / "voiceover_cleanup.json"), cleaned_words)
    _write_json(str(root / "dead_time.json"), dead_time)
    from .content_factory import audio_first_timeline
    _write_json(str(root / "audio_first_timeline.json"), audio_first_timeline(cleaned_words.get("words", words), target_seconds=target_seconds))

    crop_plan: Optional[Dict[str, Any]] = None
    if scenes:
        first = scenes[0]
        width = int(first.get("width", 1920) or 1920)
        height = int(first.get("height", 1080) or 1080)
        subjects = first.get("faces") or first.get("objects") or []
        if subjects:
            from .content_factory import get_layout
            crop_plan = plan_source_aware_crop(width, height, get_layout("shorts"), subjects)
    _write_json(str(root / "source_aware_crop.json"), crop_plan or {"mode": "center_fallback"})

    thumbnail_rank = {"ranked": [], "selected": None}
    thumbnail_dir = Path(primary_result.package_dir) / "thumbnails"
    variants = sorted(str(p) for p in thumbnail_dir.glob("*.png")) if thumbnail_dir.exists() else []
    if variants:
        thumbnail_rank = _ranked_thumbnail(str(root), variants, topic)
    _write_json(str(root / "thumbnail_ranking.json"), thumbnail_rank)

    formats = provider_retry(lambda: render_all_platforms(primary_result.final_video, str(root), platforms, crop_plan=crop_plan), attempts=3)
    long_form = provider_retry(lambda: render_long_form_master(input_video or primary_result.final_video, str(root)), attempts=3)
    clip_count = max(5, min(20, int(clip_count if clip_count is not None else choose_clip_count(len(words), 0.6, target_seconds))))
    # Repurposing must operate on the full original file, not the short already
    # composed by the primary pipeline. When the workflow starts from a topic
    # only, the primary render is the intended source.
    clip_source = _resolve_clip_source(input_video, primary_result.final_video)
    clip_probe = _probe(clip_source)
    try:
        source_duration = float((clip_probe.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError, OverflowError):
        source_duration = 0.0
    clip_windows = (
        _clip_window_candidates(scenes, scored_shots, clip_count, source_duration)
        if source_duration > 0.5 and scenes and scored_shots
        else None
    )
    clips = provider_retry(
        lambda: render_clip_factory(
            clip_source, str(root), count=clip_count, crop_plan=crop_plan, windows=clip_windows
        ),
        attempts=3,
    )

    clip_candidate_report: Dict[str, Any] = {
        "schema_version": 1,
        "source_video": clip_source,
        "selection_method": "unknown",
        "candidates": [],
    }
    try:
        clip_candidate_report = json.loads((root / "clip_candidates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass

    # Attach the evidence behind each ranked candidate so a human can review
    # why it was selected without mistaking heuristic scores for performance predictions.
    scored_by_id = {
        str(item.get("shot_id", "")): item
        for item in scored_shots
        if isinstance(item, Mapping)
    }
    if clip_candidate_report.get("selection_method") == "scene-ranked-diverse":
        for candidate in clip_candidate_report.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            try:
                center = (
                    float(candidate.get("source_start_seconds", 0.0))
                    + float(candidate.get("source_end_seconds", 0.0))
                ) / 2.0
            except (TypeError, ValueError, OverflowError):
                continue
            nearest = None
            nearest_distance = float("inf")
            for scene in scenes:
                try:
                    scene_start = float(scene.get("start", scene.get("start_time", scene.get("timestamp", 0.0))) or 0.0)
                    scene_end = float(scene.get("end", scene.get("end_time", scene_start)) or scene_start)
                except (TypeError, ValueError, OverflowError):
                    continue
                scene_center = (scene_start + scene_end) / 2.0
                distance = abs(scene_center - center)
                if distance < nearest_distance:
                    nearest, nearest_distance = scene, distance
            if nearest is not None:
                scene_id = str(nearest.get("id", ""))
                evidence = scored_by_id.get(scene_id)
                candidate["source_scene_id"] = scene_id
                if evidence:
                    candidate["scene_score"] = evidence.get("score")
                    candidate["score_evidence"] = {
                        key: evidence[key]
                        for key in (
                            "motion", "faces", "brightness", "uniqueness",
                            "audio_relevance", "semantic_similarity",
                        )
                        if key in evidence
                    }
                    candidate["selection_reason"] = (
                        "Selected from a high-ranked source scene; component scores are explainable "
                        "heuristics, not estimates of views, clicks, or retention."
                    )
    _write_json(str(root / "clip_candidates.json"), clip_candidate_report)
    checkpoints.mark("renders", status="complete", artifacts=list(formats.values()) + [long_form] + list(clips))

    captions_json = Path(primary_result.package_dir) / "caption_choreography.json"
    chapters: list[Dict[str, Any]] = []
    if captions_json.exists():
        try:
            payload = json.loads(captions_json.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                chapters = generate_chapters(payload)
        except Exception:
            chapters = []
    _write_json(str(root / "chapters.json"), chapters)

    dependency = artifact_dependency_graph(_build_dependency_inputs(topic, research, script, primary_result.final_video, formats, clips, clip_source=clip_source))
    fingerprint = duplicate_fingerprint(script=script, title=topic, clip_paths=clips)
    manifest: Dict[str, Any] = {
        "version": 5, "status": "complete", "topic": topic,
        "primary": primary_result.package_dir, "primary_video": primary_result.final_video,
        "long_form_master": long_form, "platform_renders": formats, "short_clips": clips,
        "clip_source": clip_source,
        "clip_candidates": clip_candidate_report.get("candidates", []),
        "clip_selection_method": clip_candidate_report.get("selection_method", "unknown"),
        "clip_count": len(clips), "research": research, "hook_candidates": hooks,
        "pacing": pacing, "shot_scores": scored_shots[:20], "visual_redundancy": redundancy,
        "dead_time": dead_time, "voiceover_cleanup": cleaned_words,
        "fact_check_review": _fact_check_review(script, research), "thumbnail_ranking": thumbnail_rank,
        "fingerprint": fingerprint, "artifact_dependency_graph": dependency,
        "deterministic": deterministic_manifest(job_id=root.name, inputs={"topic": topic, "input_video": os.path.abspath(input_video) if input_video else None}, selected_assets=[str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and p.name != "complete_factory_manifest.json"], random_seed=0, model_versions={"runtime": "5"}),
        "render_validation": {platform: _validate_output(path, width=PLATFORM_LAYOUTS[platform][0], height=PLATFORM_LAYOUTS[platform][1]) for platform, path in formats.items()},
        "long_form_validation": _validate_output(long_form, width=1920, height=1080),
        "clip_validation": {os.path.basename(path): _validate_output(path, width=1080, height=1920) for path in clips},
    }

    from .upload_package import finalize_upload_package
    upload_packages: Dict[str, Any] = {}
    for platform, video_path in formats.items():
        upload_packages[platform] = finalize_upload_package(
            str(root), topic=topic,
            summary={
                **research,
                "hooks": hooks,
                "hook": hooks[0]["hook"] if hooks else "",
                "strongest_angle": topic,
                "emotion": research.get("emotion", {}).get("emotion", ""),
                "source_metadata": {
                    "creator": str((youtube_options or {}).get("source_creator", "")).strip(),
                    "title": str((youtube_options or {}).get("source_title", "")).strip(),
                    "url": str((youtube_options or {}).get("source_url", "")).strip(),
                    "rights_basis": str((youtube_options or {}).get("rights_basis", "")).strip().lower(),
                    "rights_status": str((youtube_options or {}).get("rights_status") or (youtube_options or {}).get("rights_basis", "")).strip().lower(),
                    "license_url": str((youtube_options or {}).get("license_url", "")).strip(),
                    "evidence_url": str((youtube_options or {}).get("evidence_url", "")).strip(),
                    "declared_by": str((youtube_options or {}).get("rights_declared_by") or os.environ.get("AIVF_RIGHTS_DECLARED_BY") or "local-user").strip(),
                    "declared_at": str((youtube_options or {}).get("rights_declared_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
                    "source": "user_provided",
                },
                "requires_rights_declaration": bool(publish_youtube),
            },
            script=script, platform=platform, final_video=video_path, thumbnail=thumbnail_rank.get("selected"),
            caption_path=str(Path(primary_result.package_dir) / "captions.ass") if Path(primary_result.package_dir, "captions.ass").exists() else None,
        )
    manifest["upload_packages"] = upload_packages
    artifact_readiness: Dict[str, Any] = {}
    for platform, video_path in formats.items():
        width, height = PLATFORM_LAYOUTS[platform]
        readiness = evaluate_artifact(
            video_path,
            target_seconds=target_seconds,
            platform_profile={"width": width, "height": height},
            package_dir=str(root),
            upload_package_required=True,
            publish_required=publish_youtube,
            publish_prerequisites_met=bool((youtube_options or {}).get("disclosure_reviewed", False)) if publish_youtube else False,
            metadata_guardrails_ok=bool((upload_packages.get(platform, {}).get("metadata_quality") or {}).get("passed", False)),
        )
        artifact_readiness[platform] = readiness.to_dict()
    manifest["artifact_readiness"] = artifact_readiness
    _write_json(str(root / "complete_factory_manifest.json"), manifest)

    if learning_history_path:
        manifest["learning_record"] = update_learning_history(learning_history_path, topic=topic, platform="youtube_shorts", package_dir=str(root), metrics={"hook_duration": pacing[0] if pacing else 2.0})
        _write_json(str(root / "complete_factory_manifest.json"), manifest)

    if publish_youtube:
        opts = dict(youtube_options or {})
        short_package = upload_packages.get("youtube_shorts", {})
        if not short_package.get("publish_ready", False):
            raise RuntimeError(
                "YouTube publishing blocked: third-party media rights are not cleared; "
                "verify ownership, permission, license, or public-domain status first."
            )
        from .youtube_publisher import add_video_to_playlist, ensure_playlist, fetch_video_analytics, fetch_video_statistics, upload_video
        caption_file = short_package.get("files", {}).get("captions_srt")
        disclosure_reviewed = bool(opts.get("disclosure_reviewed", False))
        upload_result = upload_video(
            formats.get("youtube_shorts", primary_result.final_video), title=str(short_package.get("selected_title", topic)),
            description=str(short_package.get("description", "")), tags=short_package.get("tags", []),
            privacy_status=str(opts.get("privacy", "private")), thumbnail_path=thumbnail_rank.get("selected"),
            caption_path=str(root / caption_file) if caption_file else None,
            client_secrets_path=opts.get("client_secrets_path"), token_path=opts.get("token_path"),
            ai_generated=bool(opts.get("ai_generated", False)), realistic_alteration=bool(opts.get("realistic_alteration", False)), disclosure_reviewed=disclosure_reviewed,
        )
        manifest["youtube_upload"] = upload_result
        playlist_title = opts.get("playlist_title")
        if playlist_title:
            playlist = ensure_playlist(str(playlist_title), description=str(opts.get("playlist_description", "")), privacy_status=str(opts.get("playlist_privacy", "private")), client_secrets_path=opts.get("client_secrets_path"), token_path=opts.get("token_path"))
            manifest["playlist"] = add_video_to_playlist(upload_result["video_id"], playlist["id"], client_secrets_path=opts.get("client_secrets_path"), token_path=opts.get("token_path"))
        video_id = str(upload_result["video_id"])
        stats = fetch_video_statistics(video_id, client_secrets_path=opts.get("client_secrets_path"), token_path=opts.get("token_path"))
        manifest["performance_feedback"] = stats
        if learning_history_path:
            published_at = str((stats.get("snippet") or {}).get("publishedAt") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
            stats_payload = stats.get("statistics") or {}
            performance_metrics: Dict[str, Any] = {}
            for output_key, source_key in (
                ("views", "viewCount"),
                ("likes", "likeCount"),
                ("comments", "commentCount"),
            ):
                if stats_payload.get(source_key) is not None:
                    performance_metrics[output_key] = float(stats_payload[source_key] or 0.0)
            start_date = published_at[:10]
            end_date = datetime.now(timezone.utc).date().isoformat()
            try:
                analytics = fetch_video_analytics(
                    video_id,
                    start_date=start_date,
                    end_date=end_date,
                    client_secrets_path=opts.get("client_secrets_path"),
                    token_path=opts.get("token_path"),
                )
                rows = analytics.get("rows") if isinstance(analytics, Mapping) else None
                headers = analytics.get("columnHeaders") if isinstance(analytics, Mapping) else None
                raw: Dict[str, Any] = {}
                if isinstance(rows, list) and rows and isinstance(headers, list):
                    first_row = rows[0]
                    names = [
                        str(header.get("name") or "")
                        for header in headers if isinstance(header, Mapping)
                    ]
                    if isinstance(first_row, (list, tuple)) and len(names) == len(first_row) == len(headers) and all(names):
                        raw = dict(zip(names, first_row))
                    elif isinstance(first_row, Mapping):
                        raw = dict(first_row)
                for metric in ("views", "likes", "comments"):
                    if raw.get(metric) is not None:
                        performance_metrics[metric] = float(raw[metric])
                if raw.get("averageViewPercentage") is not None:
                    performance_metrics["averageViewPercentage"] = float(raw["averageViewPercentage"])
            except Exception:
                # Public Data API counts remain usable. Missing retention stays
                # absent instead of becoming a synthetic zero label.
                pass
            update_learning_history(
                learning_history_path,
                topic=topic,
                platform="youtube_shorts",
                package_dir=str(root),
                metrics={},
                performance_metrics=performance_metrics,
                video_id=video_id,
                published_at=published_at,
            )
        _write_json(str(root / "complete_factory_manifest.json"), manifest)

    checkpoints.mark("complete", status="complete", artifacts=[str(root / "complete_factory_manifest.json")])
    return manifest


def _probe_duration(path: str) -> float:
    payload = _probe(path)
    try:
        return float((payload.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def run_autonomous_queue(queue_path: str, *, output_root: str, input_video: Optional[str], experiment_history_path: Optional[str] = None, max_jobs: Optional[int] = None) -> Dict[str, Any]:
    from .content_factory import run_queue
    def producer(job: Mapping[str, Any]) -> Dict[str, Any]:
        job_dir = os.path.join(output_root, str(job.get("id", "job")))
        return run_complete_factory(input_video, str(job["topic"]), job_dir, target_seconds=float(job.get("options", {}).get("target_seconds", 45.0)), platforms=job.get("options", {}).get("platforms", list(PLATFORM_LAYOUTS)), experiment_history_path=experiment_history_path)
    return run_queue(queue_path, producer, max_jobs=max_jobs)


__all__ = ["PLATFORM_LAYOUTS", "render_all_platforms", "render_long_form_master", "render_clip_factory", "update_learning_history", "run_complete_factory", "run_autonomous_queue"]
