"""Executable end-to-end content-factory orchestration."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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
from .learning_recommender import load_experiments
from .production_models import ProductionResult
from .production_pipeline import run_production_pipeline

PLATFORM_LAYOUTS = {
    "youtube_shorts": (1080, 1920), "tiktok": (1080, 1920),
    "instagram_reels": (1080, 1920), "youtube": (1920, 1080), "square": (1080, 1080),
}


def _write_json(path: str, payload: Any) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return path


def _run(cmd: Sequence[str]) -> None:
    completed = subprocess.run(list(cmd), capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "ffmpeg failed").strip())


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _probe(path: str) -> Dict[str, Any]:
    if not shutil.which("ffprobe"):
        return {"available": False, "warning": "ffprobe unavailable"}
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-show_entries", "stream=index,codec_type,codec_name,width,height,duration",
        "-of", "json", path,
    ], capture_output=True, text=True)
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


def _clip_window_candidates(scenes: Sequence[Mapping[str, Any]], scored: Sequence[Mapping[str, Any]], count: int, duration: float) -> list[tuple[float, float]]:
    by_id = {str(scene.get("id", index)): scene for index, scene in enumerate(scenes)}
    windows: list[tuple[float, float]] = []
    default_len = min(35.0, max(12.0, duration / max(1, min(count, 6))))
    for item in scored[:count]:
        scene = by_id.get(str(item.get("shot_id")))
        if not scene:
            continue
        start = float(scene.get("start", scene.get("start_time", scene.get("timestamp", 0.0))) or 0.0)
        end = float(scene.get("end", scene.get("end_time", start + default_len)) or (start + default_len))
        center = (start + end) / 2.0
        clip_len = min(35.0, max(12.0, end - start if end > start else default_len))
        clip_start = max(0.0, center - clip_len / 2.0)
        clip_end = min(duration, clip_start + clip_len)
        clip_start = max(0.0, clip_end - clip_len)
        windows.append((round(clip_start, 3), round(clip_end - clip_start, 3)))
    return windows


def render_clip_factory(source: str, package_dir: str, *, count: int, crop_plan: Optional[Mapping[str, Any]] = None, windows: Optional[Sequence[tuple[float, float]]] = None) -> list[str]:
    if not _ffmpeg_available():
        raise RuntimeError("ffmpeg is required for the clip factory")
    probe = _probe(source)
    try:
        duration = float((probe.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0.5:
        return []
    count = max(5, min(20, int(count)))
    if windows:
        starts = list(windows[:count])
    else:
        clip_len = min(35.0, max(12.0, duration / max(1, min(count, 6))))
        max_start = max(0.0, duration - clip_len)
        starts = [(round(max_start * i / max(1, count - 1), 3), min(clip_len, duration - round(max_start * i / max(1, count - 1), 3))) for i in range(count)]
    while len(starts) < count:
        clip_len = min(35.0, max(12.0, duration))
        starts.append((0.0, clip_len))
    outputs: list[str] = []
    for index, (start, length) in enumerate(starts, start=1):
        out = os.path.join(package_dir, "clip_factory", f"short_{index:02d}.mp4")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        if crop_plan:
            left, top = _crop_offsets(crop_plan)
            vf = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:{left}:{top}"
        _run(["ffmpeg", "-y", "-ss", str(start), "-i", source, "-t", str(min(length, max(0.25, duration - start))), "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-movflags", "+faststart", out])
        check = _validate_output(out, width=1080, height=1920)
        if not check["ok"]:
            raise RuntimeError(f"Clip {index} validation failed: {check['errors']}")
        outputs.append(out)
    return outputs


def update_learning_history(path: str, *, topic: str, platform: str, package_dir: str, metrics: Mapping[str, Any], performance_metrics: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    history = load_experiments(path) if os.path.exists(path) else []
    record: Dict[str, Any] = {"topic": topic, "platform": platform, "package_dir": package_dir, "cuts_per_minute": metrics.get("cuts_per_minute", 0), "avg_shot_duration": metrics.get("avg_shot_duration", 0), "hook_duration": metrics.get("hook_duration", 2.0), "music_energy": metrics.get("music_energy", 0.5), "caption_style": metrics.get("caption_style", "karaoke"), "voice": metrics.get("voice", "en-US-GuyNeural")}
    if performance_metrics:
        from .content_factory import performance_feedback
        record.update(performance_feedback(performance_metrics))
        record.update({k: performance_metrics[k] for k in ("views", "likes", "comments", "averageViewPercentage") if k in performance_metrics})
    record["performance_score"] = float(record.get("engagement_rate", 0.0)) + float(record.get("retention", 0.0))
    history.append(record)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(history, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return record


def _fact_check_review(script: str, research: Mapping[str, Any]) -> Dict[str, Any]:
    lines = [line.strip() for line in script.splitlines() if line.strip()]
    claims = [line for line in lines if any(ch.isdigit() for ch in line) or any(word in line.lower() for word in ("was", "is", "won", "lost", "first", "only", "always", "never"))]
    return {"status": "review_required" if claims else "no_obvious_factual_claims", "claims_to_verify": claims[:25], "research_available": bool(research)}


def _build_dependency_inputs(topic: str, research: Mapping[str, Any], script: str, primary_video: str, formats: Mapping[str, str], clips: Sequence[str]) -> Dict[str, Any]:
    return {"topic": {"inputs": {"topic": topic}}, "research": {"inputs": {"topic": topic, "research": research}}, "script": {"inputs": {"topic": topic, "research_fingerprint": dependency_fingerprint(research)}}, "primary_video": {"inputs": {"script_fingerprint": dependency_fingerprint({"script": script}), "source": primary_video}}, "platform_renders": {"inputs": {"primary_video": primary_video, "platforms": sorted(formats)}}, "short_clips": {"inputs": {"primary_video": primary_video, "count": len(clips), "formats": sorted(formats)}}}


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
        if experiment_history_path and os.path.exists(experiment_history_path):
            research["learned_style"] = learn_channel_style(load_experiments(experiment_history_path))
        _write_json(str(root / "auto_research.json"), research)
        checkpoints.mark("research", status="complete", artifacts=[str(root / "auto_research.json")])

    if input_video is None:
        from .pipeline import PipelineContext, build_director_pipeline
        primary_dir = str(root / "primary")
        ctx = PipelineContext(topic=topic, package_dir=primary_dir, target_seconds=target_seconds, model_key=model_key)
        ctx = provider_retry(lambda: build_director_pipeline(verbose=False).run(ctx), attempts=2)
        primary_result = ProductionResult(package_dir=primary_dir)
        primary_result.final_video = ctx.final_video
        primary_result.script_path = str(Path(primary_dir) / "script.txt") if Path(primary_dir, "script.txt").exists() else None
        primary_result.metrics_path = str(Path(primary_dir) / "metrics.json") if Path(primary_dir, "metrics.json").exists() else None
        primary_result.warnings.extend(ctx.warnings)
        primary_result.errors.extend(ctx.errors)
    else:
        primary_dir = str(root / "primary")
        primary_result = provider_retry(lambda: run_production_pipeline(input_video, topic, primary_dir, target_seconds=target_seconds, research_summary=research, model_key=model_key, skip_qc=skip_qc, experiment_history_path=experiment_history_path, platform="youtube_shorts"), attempts=3)
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
    clip_windows = _clip_window_candidates(scenes, scored_shots, clip_count, _probe_duration(primary_result.final_video)) if False else None
    # Prefer highest-scoring scene windows; when scene timing is unavailable, the clip factory deterministically spreads windows.
    if scenes and scored_shots:
        probe = _probe(primary_result.final_video)
        try:
            source_duration = float((probe.get("format") or {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            source_duration = 0.0
        clip_windows = _clip_window_candidates(scenes, scored_shots, clip_count, source_duration) if source_duration else None
    clips = provider_retry(lambda: render_clip_factory(primary_result.final_video, str(root), count=clip_count, crop_plan=crop_plan, windows=clip_windows), attempts=3)
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

    dependency = artifact_dependency_graph(_build_dependency_inputs(topic, research, script, primary_result.final_video, formats, clips))
    fingerprint = duplicate_fingerprint(script=script, title=topic, clip_paths=clips)
    manifest: Dict[str, Any] = {
        "version": 5, "status": "complete", "topic": topic,
        "primary": primary_result.package_dir, "primary_video": primary_result.final_video,
        "long_form_master": long_form, "platform_renders": formats, "short_clips": clips,
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
            summary={**research, "hooks": hooks, "hook": hooks[0]["hook"] if hooks else "", "strongest_angle": topic, "emotion": research.get("emotion", {}).get("emotion", "")},
            script=script, platform=platform, final_video=video_path, thumbnail=thumbnail_rank.get("selected"),
            caption_path=str(Path(primary_result.package_dir) / "captions.ass") if Path(primary_result.package_dir, "captions.ass").exists() else None,
        )
    manifest["upload_packages"] = upload_packages
    _write_json(str(root / "complete_factory_manifest.json"), manifest)

    if experiment_history_path:
        manifest["learning_record"] = update_learning_history(experiment_history_path, topic=topic, platform="youtube_shorts", package_dir=str(root), metrics={"hook_duration": pacing[0] if pacing else 2.0})
        _write_json(str(root / "complete_factory_manifest.json"), manifest)

    if publish_youtube:
        opts = dict(youtube_options or {})
        from .youtube_publisher import add_video_to_playlist, ensure_playlist, fetch_video_statistics, upload_video
        short_package = upload_packages.get("youtube_shorts", {})
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
        stats = fetch_video_statistics(upload_result["video_id"], client_secrets_path=opts.get("client_secrets_path"), token_path=opts.get("token_path"))
        manifest["performance_feedback"] = stats
        if experiment_history_path:
            update_learning_history(experiment_history_path, topic=topic, platform="youtube_shorts", package_dir=str(root), metrics={}, performance_metrics=stats.get("statistics", {}))
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
