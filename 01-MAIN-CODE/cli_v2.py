"""CLI for AI Video Factory v2.

Usage examples:
    python cli_v2.py "Minecraft betrayal on SMP" --raw-video clip.mp4 --production
    python cli_v2.py "Minecraft betrayal" --template minecraft_betrayal
"""

import argparse
import logging
import os
import re
import sys

from ai_video_factory.config import AIVFConfig
from ai_video_factory.nle_export_v2 import export_all_nle_formats
from ai_video_factory.pipeline import PipelineContext, build_director_pipeline
from ai_video_factory.production_pipeline import run_production_pipeline
from ai_video_factory.quality_control_v2 import run_enhanced_qc
from ai_video_factory.subtitle_renderer import burn_subtitles

logger = logging.getLogger("ai_video_factory.cli_v2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _safe_dict(value):
    return value if isinstance(value, dict) else {}


def _safe_package_name(topic: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", topic.strip()).strip("._-")
    return (value[:70] or "video")


def main():
    parser = argparse.ArgumentParser(
        description="AI Video Factory v2 — generate upload-ready short video packages",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("topic", help="Video topic or title")
    parser.add_argument("--out", default="output", help="Output root folder")
    parser.add_argument("--raw-video", default=None, help="Path to raw footage for auto-edit")
    parser.add_argument("--production", action="store_true", help="Use the footage-aware end-to-end production pipeline")
    parser.add_argument("--ocr", action="store_true", help="Enable optional OCR during scene analysis")
    parser.add_argument("--diarization", action="store_true", help="Enable pyannote speaker diarization (requires Hugging Face token)")
    parser.add_argument("--download-assets", action="store_true", help="Download missing external runtime model assets")
    parser.add_argument("--director", action="store_true", help="Use the legacy director pipeline")
    parser.add_argument("--pipeline", default="default", choices=["default", "fast", "package_only"], help="Pipeline preset to use")
    parser.add_argument("--template", default=None, help="Use a template (skips research)")
    parser.add_argument("--style", default="gaming_fast", choices=["gaming_fast", "gaming_cinematic", "tutorial"], help="Style profile")
    parser.add_argument("--subtitle-style", default="bold_white", choices=["bold_white", "bold_yellow", "elegant_white", "clear_white", "karaoke"], help="Subtitle burn-in style")
    parser.add_argument("--export-nle", default=None, choices=["resolve", "premiere", "capcut", "all"], help="Export to NLE format after rendering")
    parser.add_argument("--skip-stages", default="", help="Comma-separated stages to skip")
    parser.add_argument("--use-groq", action="store_true", help="Enable Groq research enrichment")
    parser.add_argument("--groq-key", default=None, help="Groq API key")
    parser.add_argument("--model-key", default=None, help="Model API key")
    parser.add_argument("--target-seconds", type=float, default=45.0, help="Target video length")
    parser.add_argument("--skip-qc", action="store_true", help="Skip quality control")
    parser.add_argument("--interactive", action="store_true", help="Interactive review")
    parser.add_argument("--review", action="store_true", help="Run automated review")
    parser.add_argument("--auto-fix", action="store_true", help="Auto-fix issues")
    parser.add_argument("--elevenlabs-key", default=None, help="ElevenLabs API key")
    parser.add_argument("--learn", action="store_true", help="Enable feedback tracking loop")
    parser.add_argument("--engagement-score", type=float, default=None, help="Provide engagement score (0.0-1.0) to update the learning tracker")
    args = parser.parse_args()

    if not args.topic or len(args.topic.strip()) < 2:
        parser.error("Topic must be at least 2 characters.")
    if not args.raw_video and args.production:
        parser.error("--production requires --raw-video")
    if args.raw_video and not os.path.exists(args.raw_video):
        parser.error(f"Raw video not found: {args.raw_video}")
    if not 15 <= args.target_seconds <= 120:
        parser.error("Target seconds must be between 15 and 120.")

    if args.download_assets:
        from ai_video_factory.asset_manager import install_runtime_assets
        assets = install_runtime_assets(download_missing=True)
        unavailable = [name for name, info in assets.items() if not info["available"]]
        if unavailable:
            parser.error("Runtime asset installation failed: " + ", ".join(unavailable))
        logger.info("Runtime assets ready")

    config = AIVFConfig.load()
    config.output_root = args.out
    config.active_pipeline = args.pipeline
    config.active_style = args.style

    if args.groq_key:
        config.set_api_key("groq", args.groq_key)
    if args.elevenlabs_key:
        config.set_api_key("elevenlabs", args.elevenlabs_key)
    if args.model_key:
        config.set_api_key("openai", args.model_key)

    if args.production:
        package_dir = os.path.join(args.out, _safe_package_name(args.topic))
        result = run_production_pipeline(
            args.raw_video,
            args.topic,
            package_dir,
            target_seconds=args.target_seconds,
            enable_ocr=args.ocr,
            model_key=config.api_keys.openai or os.environ.get("OPENAI_API_KEY"),
            skip_qc=args.skip_qc,
            enable_diarization=args.diarization,
            diarization_token=os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("PYANNOTE_AUTH_TOKEN"),
        )
        if result.errors:
            logger.error("Production pipeline completed with errors:")
            for error in result.errors:
                logger.error("  - %s", error)
            sys.exit(1)
        for warning in result.warnings:
            logger.warning("  ! %s", warning)
        logger.info("Production pipeline complete!")
        logger.info("Package: %s", result.package_dir)
        logger.info("Final video: %s", result.final_video)
        logger.info("Timeline: %s", result.timeline_path)
        logger.info("Scenes: %s", result.scenes_path)
        return

    skip_stages = [s.strip() for s in args.skip_stages.split(",") if s.strip()]
    if args.template:
        skip_stages.append("research")

    pipeline = build_director_pipeline(skip_stages=skip_stages)
    ctx = PipelineContext(
        topic=args.topic,
        raw_video=args.raw_video,
        target_seconds=args.target_seconds,
        skip_qc=args.skip_qc,
        use_groq=args.use_groq,
        model_key=config.api_keys.openai or os.environ.get("OPENAI_API_KEY"),
        groq_key=config.api_keys.groq or os.environ.get("GROQ_API_KEY"),
    )
    ctx = pipeline.run(ctx)

    if ctx.errors:
        logger.error("Pipeline completed with errors:")
        for err in ctx.errors:
            logger.error("  - %s", err)
        sys.exit(1)

    logger.info("Pipeline complete! Package: %s", ctx.package_dir)

    if ctx.final_video and ctx.script and args.subtitle_style:
        try:
            sub_out = os.path.join(ctx.package_dir, "final_with_subtitles.mp4")
            burn_subtitles(ctx.final_video, sub_out, ctx.script, style=args.subtitle_style)
            logger.info("Subtitled video: %s", sub_out)
        except Exception as exc:
            logger.warning("Subtitle burn-in failed: %s", exc)

    if args.export_nle and ctx.package_dir:
        try:
            clips_dir = os.path.join(ctx.package_dir, "_clips")
            clips = [os.path.join(clips_dir, c) for c in sorted(os.listdir(clips_dir)) if c.endswith(".mp4")] if os.path.exists(clips_dir) else []
            if clips:
                logger.info("NLE exports: %s", export_all_nle_formats(ctx.package_dir, clips, ctx.plan))
        except Exception as exc:
            logger.warning("NLE export failed: %s", exc)

    if not args.skip_qc and ctx.package_dir:
        try:
            qc = run_enhanced_qc(ctx.package_dir, research=ctx.research, script=ctx.script)
            logger.info("QC report: %s", os.path.join(ctx.package_dir, "qc_report_v2.json"))
            if qc.get("warnings"):
                logger.warning("Warnings: %s", len(qc["warnings"]))
        except Exception as exc:
            logger.warning("Enhanced QC failed: %s", exc)

    if args.learn and ctx.package_dir:
        try:
            from ai_video_factory.knowledge_v2 import RealKnowledgeBase, VideoFeatures
            kb = RealKnowledgeBase(root_dir=config.knowledge_root)
            metrics = _safe_dict(getattr(ctx, "metrics", None))
            qc_report = _safe_dict(getattr(ctx, "qc_report", None))
            plan = _safe_dict(getattr(ctx, "plan", None))
            feature_vector = VideoFeatures(
                topic=args.topic,
                filter_count=int(metrics.get("filter_count", 0)),
                avg_shot_duration=float(metrics.get("avg_shot_duration", 0.0)),
                cuts_per_minute=float(metrics.get("cuts_per_minute", 0.0)),
                has_voiceover=bool(getattr(ctx, "voiceover", None) is not None),
                has_music=bool(getattr(ctx, "music_track", None) is not None),
                shot_variety_ratio=float(0.75 if qc_report.get("checks", {}).get("shot_variance_ok", True) else 0.5),
                filters_used={f: True for f in plan.get("filters_used", [])},
            )
            if args.engagement_score is not None:
                kb.learn_from_feedback(package_id=ctx.package_dir, engagement_score=args.engagement_score, features=feature_vector)
                logger.info("Feedback tracker updated with score: %.2f", args.engagement_score)
                logger.info("Predicted engagement for similar config: %.2f", kb.predict_engagement(feature_vector))
            else:
                logger.info("Tip: use --engagement-score 0.85 to update the feedback tracker.")
            report = kb.get_learning_report()
            logger.info("Feedback tracker report: %s records, %s filters tracked", report["total_feedback_records"], len(report["filters_tracked"]))
        except Exception as exc:
            logger.warning("Learning update failed: %s", exc)

    logger.info("Done.")


if __name__ == "__main__":
    main()
