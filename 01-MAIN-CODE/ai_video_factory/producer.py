"""Higher-level producer utilities for the AI Video Factory.

This module provides a single entry function `produce_package` which
runs the full research -> idea -> thumbnail -> packaging flow and
exposes a `use_groq` hook for optional trend/angle enrichment.

Usage example:
    from ai_video_factory.producer import produce_package
    produce_package("Minecraft betrayal on SMP", use_groq=True, groq_api_key=os.getenv("GROQ_API_KEY"))
"""
import logging
from typing import Optional
import os
from datetime import datetime

from .research import research_topic
from .plan import make_idea, make_idea_with_knowledge
from .knowledge import KnowledgeBase
from .thumbnail import make_thumbnail, make_thumbnail_variants
from .output_packager import write_package
from .visuals_fetcher import download_visuals

logger = logging.getLogger(__name__)


def produce_package(
    topic: str,
    out_root: str = "output",
    use_groq: bool = False,
    groq_api_key: Optional[str] = None,
    thumbnail_subject: Optional[str] = None,
    enable_full_downloads: bool = False,
    allow_install_yt_dlp: bool = False,
    model_api_key: Optional[str] = None,
    use_spacy_persona: bool = False,
    prune_min_match: float = 0.25,
    prune_min_motion: float = 0.07,
    prune_require_faces: Optional[list] = None,
    prune_min_model_confidence: float = 0.0,
    target_total_seconds: float = 45.0,
    config_path: Optional[str] = None,
) -> str:
    """Produce an upload-ready package for `topic`.

    Steps performed:
      1. Research topic (Wikipedia + web + optional Groq)
      2. Generate idea/hook/script via `plan.make_idea`
      3. Create thumbnail variants and pick a primary thumbnail
      4. Write package files using `output_packager.write_package`

    The function is defensive and will still produce a package even if
    some optional enrichment fails.
    """
    # load per-project config if present and merge (config keys override defaults unless CLI provided)
    cfg = {}
    try:
        import json

        # prefer explicit config_path, else look for aivf_config.json or .aivf.json in cwd
        if config_path and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as cf:
                cfg = json.load(cf)
        else:
            for candidate in ("aivf_config.json", ".aivf.json"):
                if os.path.exists(candidate):
                    with open(candidate, "r", encoding="utf-8") as cf:
                        cfg = json.load(cf)
                    break
    except Exception as e:
        logger.warning("Config load failed: %s", e)
        cfg = {}

    # apply cfg defaults where not provided explicitly via function args
    try:
        if cfg:
            prune_min_match = float(cfg.get("prune_min_match", prune_min_match))
            prune_min_motion = float(cfg.get("prune_min_motion", prune_min_motion))
            if prune_require_faces is None:
                prune_require_faces = cfg.get("prune_require_faces", prune_require_faces)
            use_spacy_persona = bool(cfg.get("use_spacy_persona", use_spacy_persona))
            target_total_seconds = float(cfg.get("target_total_seconds", target_total_seconds))
    except Exception as e:
        logger.warning("Config merge failed: %s", e)

    summary = research_topic(topic, use_groq=use_groq, groq_api_key=groq_api_key)
    summary["target_total_seconds"] = target_total_seconds
    
    # Use knowledge-enhanced idea generation if available
    try:
        kb = KnowledgeBase()
        expertise = kb.get_topic_expertise(topic)
        trending = kb.get_trending_techniques()
        idea = make_idea_with_knowledge(summary, topic, expertise, trending)
    except Exception as e:
        logger.warning("Knowledge-enhanced idea generation failed: %s", e)
        # Fallback to basic idea if knowledge system unavailable
        idea = make_idea(summary)

    subject = thumbnail_subject or (idea.get("title_options") or [topic])[0]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    import re

    # sanitize topic to a filesystem-safe short string
    safe_topic = re.sub(r"[^A-Za-z0-9_-]", "_", topic)[:40]
    pkg_dir = os.path.join(out_root, f"{safe_topic}_{timestamp}")
    os.makedirs(pkg_dir, exist_ok=True)

    # create several thumbnail variants and pick the first as the primary
    try:
        vars_dir = os.path.join(pkg_dir, "thumbnails")
        variants = make_thumbnail_variants(subject, vars_dir, count=3)
        primary = variants[0] if variants else ""
        # also create an explicit main thumbnail size (fallback)
        main_thumb = os.path.join(pkg_dir, "thumbnail.png")
        make_thumbnail(subject, main_thumb, size=(1280, 720))
        # create vertical thumbnail for Shorts
        try:
            from .thumbnail import make_thumbnail_vertical

            main_thumb_vertical = os.path.join(pkg_dir, "thumbnail_vertical.png")
            make_thumbnail_vertical(subject, main_thumb_vertical, size=(1080, 1920))
        except Exception as e:
            logger.warning("Vertical thumbnail generation failed: %s", e)
            main_thumb_vertical = ""
    except Exception as e:
        logger.warning("Thumbnail generation failed: %s", e)
        primary = ""
        main_thumb = ""
        main_thumb_vertical = ""

    # write package files (script, metadata, research)
    write_package(pkg_dir, summary, idea, main_thumb)
    # attempt persona extraction from transcript/script and save to package
    try:
        script_path = os.path.join(pkg_dir, "script.txt")
        script_text = None
        if os.path.exists(script_path):
            try:
                with open(script_path, "r", encoding="utf-8") as sf:
                    script_text = sf.read()
            except Exception as e:
                logger.warning("Script read failed for persona extraction: %s", e)
                script_text = None
        if not script_text:
            script_text = idea.get("script") or ""

        if script_text and script_text.strip():
            try:
                from .persona import extract_personas

                personas = extract_personas(script_text, use_spacy=bool(use_spacy_persona))
                import json

                with open(os.path.join(pkg_dir, "personas.json"), "w", encoding="utf-8") as pf:
                    json.dump(personas, pf, indent=2)
            except Exception as e:
                logger.warning("Persona extraction failed: %s", e)
    except Exception as e:
        logger.warning("Persona extraction cleanup failed: %s", e)
    # ensure vertical thumbnail path is saved (some workflows expect thumbnail_vertical.png)
    if main_thumb_vertical:
        try:
            import shutil

            dstv = os.path.join(pkg_dir, os.path.basename(main_thumb_vertical))
            shutil.copyfile(main_thumb_vertical, dstv)
        except Exception as e:
            logger.warning("Vertical thumbnail copy failed: %s", e)

    # download supporting visuals (best-effort) and save metadata with provenance
    try:
        visuals = summary.get("visuals") or []
        if visuals:
            vis_dir = os.path.join(pkg_dir, "visuals")
            os.makedirs(vis_dir, exist_ok=True)
            # attempt model-driven vetting if a model API key is provided
            try:
                if model_api_key:
                    from .visuals_fetcher import vet_with_model

                    visuals = vet_with_model(visuals, summary, model_api_key)
                else:
                    from .visuals_fetcher import annotate_purposes

                    visuals = annotate_purposes(visuals, summary)
            except Exception as e:
                logger.warning("Visual vetting failed: %s", e)

            # optionally skip if yt-dlp is missing; do not install deps at runtime
            if allow_install_yt_dlp:
                try:
                    import shutil

                    if not shutil.which("yt-dlp"):
                        logger.warning("yt-dlp is missing; skipping clip downloads")
                except Exception as e:
                    logger.warning("yt-dlp availability check failed: %s", e)

            # download visuals; if full downloads enabled, allow youtube clip downloads
            saved = download_visuals(visuals, vis_dir, download_clips=bool(enable_full_downloads), clip_max_duration=30)
            # post-download: prune visuals by thresholds to keep high-quality, purposeful assets
            try:
                from .visuals_fetcher import prune_visuals

                req_faces = prune_require_faces if prune_require_faces is not None else ["hook", "payoff", "conflict"]
                # allow per-purpose motion thresholds from config
                motion_thresholds = None
                try:
                    motion_thresholds = cfg.get("prune_motion_by_purpose")
                except Exception as e:
                    logger.warning("Motion threshold config lookup failed: %s", e)
                    motion_thresholds = None
                kept, removed = prune_visuals(saved, min_match_score=float(prune_min_match), min_motion=float(prune_min_motion), require_faces_for=req_faces, min_model_confidence=float(prune_min_model_confidence), motion_thresholds=motion_thresholds)
                saved = kept
                try:
                    import json

                    with open(os.path.join(vis_dir, "visuals.json"), "w", encoding="utf-8") as vf:
                        json.dump(saved, vf, indent=2)
                    with open(os.path.join(vis_dir, "visuals_pruned.json"), "w", encoding="utf-8") as pf:
                        json.dump({"removed": removed}, pf, indent=2)
                except Exception as e:
                    logger.warning("Visual pruning write failed: %s", e)
            except Exception as e:
                logger.warning("Visual pruning fallback failed: %s", e)
                # fallback: just write visuals.json
                try:
                    import json

                    with open(os.path.join(vis_dir, "visuals.json"), "w", encoding="utf-8") as vf:
                        json.dump(saved, vf, indent=2)
                except Exception as log_error:
                    logger.warning("Fallback visuals.json write failed: %s", log_error)
    except Exception as e:
        logger.warning("Visual download pipeline failed: %s", e)

    # Enforce a strong short hook at the start of the script
    try:
        from tools.enforce_hook import ensure_hook_first, generate_hook_variants
        from tools.visual_hook_generator import generate_visual_hook

        script_path = os.path.join(pkg_dir, "script.txt")
        if os.path.exists(script_path):
            with open(script_path, "r", encoding="utf-8") as f:
                text = f.read()

            hook = idea.get("hook", "")
            if hook:
                text = ensure_hook_first(text, hook)
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(text)

            vhook = generate_visual_hook(pkg_dir)
            if vhook:
                lines = text.splitlines()
                rest = "\n".join(lines[1:]).lstrip()
                new_text = vhook + "\n" + rest
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write(new_text)
    except Exception as e:
        logger.warning("Hook enforcement failed: %s", e)

    # Generate A/B hook variants and write to title_options.txt
    try:
        script_path = os.path.join(pkg_dir, "script.txt")
        if os.path.exists(script_path):
            try:
                # import the generator from tools.enforce_hook
                import importlib, sys as _sys

                _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if _root not in _sys.path:
                    _sys.path.insert(0, _root)
                eh = importlib.import_module('tools.enforce_hook')
                with open(script_path, 'r', encoding='utf-8') as sf:
                    st = sf.read()
                variants = eh.generate_hook_variants(st, n=6)
                vt = [v['hook'] for v in variants]
                # write title_options.txt
                try:
                    with open(os.path.join(pkg_dir, 'title_options.txt'), 'w', encoding='utf-8') as tf:
                        for h in vt:
                            tf.write(h + '\n')
                except Exception as e:
                    logger.warning("Title options write failed: %s", e)
                # produce hook report JSON/CSV using tools.hook_report
                try:
                    import importlib
                    hr = importlib.import_module('tools.hook_report')
                    hr.main(['hook_report', pkg_dir])
                except Exception as e:
                    logger.warning("Hook report generation failed: %s", e)
            except Exception as e:
                logger.warning("Hook variants pipeline failed: %s", e)
    except Exception as e:
        logger.warning("Hook variant/report step failed: %s", e)

    return pkg_dir
