"""Command-line entry point for AI Video Factory v2."""
import argparse
import csv
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from uuid import uuid4

from ai_video_factory.config import AIVFConfig
from ai_video_factory.nle_export_v2 import export_all_nle_formats
from ai_video_factory.pipeline import PipelineContext, build_director_pipeline
from ai_video_factory.quality_control_v2 import run_enhanced_qc
from ai_video_factory.subtitle_renderer import burn_subtitles
from ai_video_factory.validation import normalize_workflow, validate_target_seconds
from ai_video_factory.upload_package import OUTPUT_PROFILES, finalize_upload_package

logger = logging.getLogger("aivf.cli")


def _stage_skips_for_pipeline(config: AIVFConfig, pipeline_name: str) -> List[str]:
    selected = config.get_pipeline(pipeline_name)
    all_names = [
        "research", "plan", "script", "thumbnail", "auto_edit",
        "voiceover", "music", "quality_control", "metadata", "metrics",
    ]
    configured = set(selected.stages)
    if "qc" in configured:
        configured.add("quality_control")
    return [name for name in all_names if name not in configured]


def _load_batch(path: str) -> List[Dict[str, object]]:
    """Load a JSON list or CSV with a required `topic` column."""
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        if path.lower().endswith(".csv"):
            return [dict(row) for row in csv.DictReader(handle) if str(row.get("topic", "")).strip()]
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("Batch JSON must contain a list")
    result: List[Dict[str, object]] = []
    for index, item in enumerate(data, start=1):
        if isinstance(item, str):
            result.append({"topic": item})
        elif isinstance(item, dict):
            result.append(dict(item))
        else:
            raise ValueError(f"Batch item {index} must be a string or object")
    return result


def _make_package_dir(output_root: str, topic: str) -> str:
    safe_topic = "_".join(topic.strip().split())
    safe_topic = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in safe_topic)[:40].strip("_") or "job"
    package_dir = os.path.join(
        output_root,
        f"{safe_topic}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}",
    )
    os.makedirs(package_dir, exist_ok=True)
    return package_dir


def _resolve_thumbnail_variant(args: argparse.Namespace, overrides: Dict[str, object], config: AIVFConfig, topic: str) -> int:
    requested = overrides.get("thumbnail_variant", args.thumbnail_variant)
    if str(requested).lower() == "auto":
        from ai_video_factory.thumbnail_learning import choose_thumbnail_variant
        selected = choose_thumbnail_variant(config.knowledge_root, topic)
        logger.info("Thumbnail learner selected variant %d for %s", selected, topic)
        return selected
    selected = int(requested)
    if selected not in {1, 2, 3}:
        raise ValueError("thumbnail_variant must be 1, 2, 3, or auto")
    return selected


def _run_one(args: argparse.Namespace, topic: str, overrides: Optional[Dict[str, object]] = None) -> int:
    overrides = overrides or {}
    topic = str(topic).strip()
    if not 2 <= len(topic) <= 200:
        logger.error("Topic must be between 2 and 200 characters: %r", topic)
        return 1

    raw_video = overrides.get("raw_video", args.raw_video)
    if raw_video:
        raw_video = str(raw_video)
        if not os.path.isfile(raw_video):
            logger.error("Raw video not found: %s", raw_video)
            return 1

    pipeline_name = normalize_workflow(str(overrides.get("pipeline", "default" if args.director else args.pipeline)))
    style = str(overrides.get("style", args.style))
    subtitle_style = str(overrides.get("subtitle_style", args.subtitle_style))
    target_seconds = validate_target_seconds(overrides.get("target_seconds", args.target_seconds))
    platform = str(overrides.get("platform", args.platform))
    if platform not in OUTPUT_PROFILES:
        logger.error("Unknown output platform/profile: %s", platform)
        return 1
    engagement_score = overrides.get("engagement_score", args.engagement_score)
    if engagement_score is not None:
        try:
            engagement_score = float(engagement_score)
        except (TypeError, ValueError):
            logger.error("Engagement score must be a number")
            return 1
        if not 0.0 <= engagement_score <= 1.0:
            logger.error("Engagement score must be between 0.0 and 1.0")
            return 1

    config = AIVFConfig.load()
    config.output_root = args.out
    config.active_pipeline = pipeline_name
    config.active_style = style
    if args.groq_key:
        config.set_api_key("groq", args.groq_key)
    if args.elevenlabs_key:
        config.set_api_key("elevenlabs", args.elevenlabs_key)
    if args.model_key:
        config.set_api_key("openai", args.model_key)

    try:
        thumbnail_variant = _resolve_thumbnail_variant(args, overrides, config, topic)
    except (OSError, ValueError, TypeError):
        logger.exception("Could not select thumbnail variant for %s", topic)
        return 1

    skip_stages = _stage_skips_for_pipeline(config, pipeline_name)
    skip_stages.extend(s.strip() for s in args.skip_stages.split(",") if s.strip())
    if overrides.get("template", args.template):
        skip_stages.append("research")

    package_dir = _make_package_dir(args.out, topic)
    start = time.monotonic()

    logger.info("============================================================")
    logger.info("AI VIDEO FACTORY — v2")
    logger.info("Topic: %s", topic)
    logger.info("Pipeline: %s", pipeline_name)
    logger.info("Style: %s", style)
    logger.info("Platform: %s", platform)
    logger.info("Thumbnail variant: %s", thumbnail_variant)
    logger.info("Package: %s", package_dir)
    logger.info("Skipped stages: %s", sorted(set(skip_stages)))

    def progress(completed: int, total: int, stage: str, elapsed: float, eta: float) -> None:
        percent = int(round((completed / max(total, 1)) * 100))
        logger.info("Progress: %3d%% — %s — elapsed %.1fs — ETA %.1fs", percent, stage, elapsed, eta)

    try:
        pipeline = build_director_pipeline(
            skip_stages=sorted(set(skip_stages)),
            verbose=False,
            progress_callback=progress,
        )
        ctx = PipelineContext(
            topic=topic,
            package_dir=package_dir,
            raw_video=str(raw_video) if raw_video else None,
            target_seconds=target_seconds,
            skip_qc=bool(overrides.get("skip_qc", args.skip_qc)),
            use_groq=bool(overrides.get("use_groq", args.use_groq)),
            model_key=config.api_keys.openai or os.environ.get("OPENAI_API_KEY"),
            groq_key=config.api_keys.groq or os.environ.get("GROQ_API_KEY"),
            thumbnail_variant=thumbnail_variant,
        )
        ctx = pipeline.run(ctx)
    except Exception:
        logger.exception("Pipeline failed for %s", topic)
        return 1

    if ctx.errors:
        logger.error("Pipeline completed with errors for %s:", topic)
        for error in ctx.errors:
            logger.error("  - %s", error)
        return 1

    logger.info("Pipeline complete in %.1fs: %s", time.monotonic() - start, ctx.package_dir)

    if ctx.final_video and ctx.script:
        try:
            sub_out = os.path.join(ctx.package_dir, "final_with_subtitles.mp4")
            burn_subtitles(ctx.final_video, sub_out, ctx.script, style=subtitle_style)
            logger.info("Subtitled video: %s", sub_out)
        except Exception:
            logger.exception("Subtitle burn-in failed for %s", topic)

    export_nle = overrides.get("export_nle", args.export_nle)
    if export_nle and ctx.package_dir:
        try:
            clips_dir = os.path.join(ctx.package_dir, "_clips")
            clips = [
                os.path.join(clips_dir, name)
                for name in sorted(os.listdir(clips_dir))
                if name.endswith(".mp4")
            ] if os.path.isdir(clips_dir) else []
            if clips:
                exports = export_all_nle_formats(ctx.package_dir, clips, ctx.plan)
                logger.info("NLE exports: %s", exports)
        except Exception:
            logger.exception("NLE export failed for %s", topic)

    skip_qc = bool(overrides.get("skip_qc", args.skip_qc))
    if not skip_qc and ctx.package_dir:
        try:
            qc = run_enhanced_qc(ctx.package_dir, research=ctx.research, script=ctx.script)
            logger.info("QC: %s", qc.get("status", "complete"))
            for warning in qc.get("warnings", [])[:5]:
                logger.warning("QC: %s", warning)
        except Exception:
            logger.exception("Enhanced QC failed for %s", topic)

    if ctx.package_dir and not getattr(args, "no_upload_package", False):
        try:
            upload_manifest = finalize_upload_package(
                ctx.package_dir,
                topic=topic,
                summary={**ctx.research, **ctx.plan, "platform": platform, "thumbnail": ctx.thumbnail},
                script=ctx.script,
                platform=platform,
                final_video=ctx.final_video,
                thumbnail=ctx.thumbnail,
                caption_path=os.path.join(ctx.package_dir, "captions.ass") if os.path.exists(os.path.join(ctx.package_dir, "captions.ass")) else None,
            )
            logger.info("Upload package ready: %s", os.path.join(ctx.package_dir, "upload"))
            logger.info("Selected title: %s", upload_manifest.get("selected_title", ""))
        except Exception:
            logger.exception("Upload package generation failed for %s", topic)

    if getattr(args, "youtube_upload", False):
        try:
            from ai_video_factory.youtube_publisher import upload_video
            manifest_path = os.path.join(ctx.package_dir, "upload", "metadata.json")
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            result = upload_video(
                ctx.final_video or os.path.join(ctx.package_dir, "final.mp4"),
                title=str(manifest.get("selected_title", topic)),
                description=str(manifest.get("description", "")),
                tags=manifest.get("tags", []),
                privacy_status=args.youtube_privacy,
                thumbnail_path=ctx.thumbnail if ctx.thumbnail else None,
                caption_path=os.path.join(ctx.package_dir, "captions.ass") if os.path.exists(os.path.join(ctx.package_dir, "captions.ass")) else None,
                client_secrets_path=args.youtube_client_secrets,
                token_path=args.youtube_token_path,
            )
            logger.info("YouTube upload complete: %s", result.get("url"))
            with open(os.path.join(ctx.package_dir, "youtube_upload.json"), "w", encoding="utf-8") as handle:
                json.dump(result, handle, indent=2, default=str)
        except Exception:
            logger.exception("YouTube upload failed for %s", topic)
            return 1

    learn = bool(overrides.get("learn", args.learn))
    if learn and engagement_score is not None and ctx.package_dir:
        try:
            from ai_video_factory.knowledge_v2 import RealKnowledgeBase, VideoFeatures

            kb = RealKnowledgeBase(root_dir=config.knowledge_root)
            features = VideoFeatures(
                topic=topic,
                filter_count=ctx.metrics.get("filter_count", 0),
                avg_shot_duration=ctx.metrics.get("avg_shot_duration", 0),
                cuts_per_minute=ctx.metrics.get("cuts_per_minute", 0),
                has_voiceover=ctx.voiceover is not None,
                has_music=ctx.music_track is not None,
                thumbnail_style=f"variant_{ctx.thumbnail_variant}",
                shot_variety_ratio=0.75 if ctx.qc_report.get("checks", {}).get("shot_variance_ok", False) else 0.5,
                filters_used={f: True for f in ctx.plan.get("filters_used", [])},
            )
            kb.learn_from_feedback(ctx.package_dir, float(engagement_score), features)
            logger.info("Learning updated with engagement score %.2f and thumbnail variant %d", float(engagement_score), ctx.thumbnail_variant)
        except Exception:
            logger.exception("Learning update failed for %s", topic)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="AI Video Factory v2 — generate upload-ready short video packages",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s "Minecraft betrayal on SMP" --raw-video gameplay.mp4 --director
  %(prog)s "COD 1v5 clutch" --raw-video clip.mp4 --pipeline fast --subtitle-style bold_yellow
  %(prog)s "Minecraft" --template minecraft_betrayal --pipeline package_only
  %(prog)s "Minecraft" --platform youtube_shorts
  %(prog)s "Minecraft" --youtube-upload --youtube-client-secrets client_secret.json
  %(prog)s --batch topics.csv --batch-workers 2
""",
    )
    parser.add_argument("topic", nargs="?", help="Video topic or title")
    parser.add_argument("--batch", default=None, help="CSV or JSON batch file with topic entries")
    parser.add_argument("--batch-workers", type=int, default=1, help="Concurrent batch jobs (default: 1)")
    parser.add_argument("--out", default="output", help="Output root folder")
    parser.add_argument("--raw-video", default=None, help="Path to raw footage for auto-edit")
    parser.add_argument("--director", action="store_true", help="Use the full director pipeline")
    parser.add_argument("--pipeline", default="default", choices=["default", "fast", "package_only"])
    parser.add_argument("--template", default=None, help="Template name; skips research")
    parser.add_argument("--style", default="gaming_fast", choices=["gaming_fast", "gaming_cinematic", "tutorial"])
    parser.add_argument("--subtitle-style", default="bold_white", choices=["bold_white", "bold_yellow", "elegant_white", "clear_white", "karaoke"])
    parser.add_argument("--platform", default="youtube_shorts", choices=sorted(OUTPUT_PROFILES), help="Output metadata/render profile")
    parser.add_argument("--no-upload-package", action="store_true", help="Skip generation of upload/ metadata files")
    parser.add_argument("--youtube-upload", action="store_true", help="Explicitly upload the finished package to YouTube")
    parser.add_argument("--youtube-client-secrets", default=None, help="OAuth client secrets JSON for YouTube")
    parser.add_argument("--youtube-token-path", default=None, help="Path for cached YouTube OAuth token")
    parser.add_argument("--youtube-privacy", default="private", choices=["private", "public", "unlisted"], help="YouTube privacy status")
    parser.add_argument("--export-nle", default=None, choices=["resolve", "premiere", "capcut", "all"])
    parser.add_argument("--skip-stages", default="", help="Comma-separated stages to skip")
    parser.add_argument("--use-groq", action="store_true", help="Enable Groq research enrichment")
    parser.add_argument("--groq-key", default=None, help="Groq API key")
    parser.add_argument("--model-key", default=None, help="Model API key")
    parser.add_argument("--elevenlabs-key", default=None, help="ElevenLabs API key")
    parser.add_argument("--target-seconds", type=float, default=45.0, help="Target video length (15-120)")
    parser.add_argument("--thumbnail-variant", default="1", choices=["1", "2", "3", "auto"], help="Thumbnail variant to use, or auto-select from learning feedback")
    parser.add_argument("--skip-qc", action="store_true", help="Skip quality control")
    parser.add_argument("--learn", action="store_true", help="Update learning system")
    parser.add_argument("--engagement-score", type=float, default=None, help="Feedback score from 0.0 to 1.0")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument("--quiet", action="store_true", help="Only show warnings and errors")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.batch_workers < 1:
        parser.error("--batch-workers must be at least 1")
    if not args.topic and not args.batch:
        parser.error("Provide a topic or --batch")

    level = logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")

    try:
        validate_target_seconds(args.target_seconds)
    except ValueError as exc:
        parser.error(str(exc))

    if not args.batch:
        return _run_one(args, args.topic or "")

    try:
        items = _load_batch(args.batch)
    except (OSError, ValueError, json.JSONDecodeError, csv.Error):
        logger.exception("Could not load batch file: %s", args.batch)
        return 1
    if not items:
        logger.error("Batch file contains no jobs")
        return 1

    logger.info("Queued %d batch jobs with %d worker(s)", len(items), args.batch_workers)
    results: List[int] = []
    with ThreadPoolExecutor(max_workers=args.batch_workers) as executor:
        futures = {
            executor.submit(_run_one, args, str(item.get("topic", "")), item): item
            for item in items
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)

    failed = sum(1 for result in results if result != 0)
    logger.info("Batch finished: %d succeeded, %d failed", len(results) - failed, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
