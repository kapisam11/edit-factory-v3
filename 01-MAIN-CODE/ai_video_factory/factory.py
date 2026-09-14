"""Orchestrator for the AI Video Factory workflow."""
import json
import logging
import os
from typing import Optional

from .producer import produce_package
from . import learning
from . import policy

logger = logging.getLogger(__name__)


def _first_existing(pkg_dir: str, names: list[str]) -> Optional[str]:
    for name in names:
        path = os.path.join(pkg_dir, name)
        if os.path.isfile(path):
            return path
    return None


def create_package(
    topic: str,
    out_root: str = "output",
    thumbnail_subject: Optional[str] = None,
    use_groq: bool = False,
    groq_api_key: Optional[str] = None,
    prune_min_match: float = 0.25,
    prune_min_motion: float = 0.07,
    prune_require_faces: Optional[list] = None,
    use_spacy_persona: bool = False,
    target_total_seconds: float = 45.0,
    config_path: Optional[str] = None,
    platform: str = "youtube_shorts",
) -> str:
    """Generate a full package and run all content-factory post-processing."""
    pkg_dir = produce_package(
        topic,
        out_root=out_root,
        use_groq=use_groq,
        groq_api_key=groq_api_key,
        thumbnail_subject=thumbnail_subject,
        prune_min_match=prune_min_match,
        prune_min_motion=prune_min_motion,
        prune_require_faces=prune_require_faces,
        use_spacy_persona=use_spacy_persona,
        target_total_seconds=target_total_seconds,
        config_path=config_path,
    )

    try:
        violations = policy.check_package(pkg_dir)
        with open(os.path.join(pkg_dir, "policy_violations.json"), "w", encoding="utf-8") as f:
            json.dump(violations, f, indent=2)
    except Exception as exc:
        logger.warning("Policy check failed: %s", exc)

    try:
        prediction = learning.predict_quality(pkg_dir)
        with open(os.path.join(pkg_dir, "quality_prediction.json"), "w", encoding="utf-8") as f:
            json.dump(prediction, f, indent=2)
    except Exception as exc:
        logger.warning("Quality prediction failed: %s", exc)

    try:
        from .quality_control import run_final_checks
        final_checks = run_final_checks(pkg_dir)
        with open(os.path.join(pkg_dir, "final_checks.json"), "w", encoding="utf-8") as f:
            json.dump(final_checks, f, indent=2)
    except Exception as exc:
        logger.warning("Final checks failed: %s", exc)

    plan = {}
    plan_path = os.path.join(pkg_dir, "plan.json")
    if os.path.isfile(plan_path):
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except Exception as exc:
            logger.warning("Could not read plan.json: %s", exc)

    final_video = _first_existing(pkg_dir, ["final.mp4", "final_short_vo.mp4", "final_short.mp4"])
    thumbnail = next(
        (
            os.path.join(pkg_dir, name)
            for name in sorted(os.listdir(pkg_dir))
            if name.lower().startswith("thumbnail") and os.path.isfile(os.path.join(pkg_dir, name))
        ),
        None,
    )
    caption_path = _first_existing(pkg_dir, ["captions.ass", "script.srt", "captions.vtt"])

    if final_video:
        try:
            from .upload_package import finalize_upload_package
            finalize_upload_package(
                pkg_dir,
                topic=topic,
                summary={**plan, "platform": platform},
                script=str(plan.get("script") or ""),
                platform=platform,
                final_video=final_video,
                thumbnail=thumbnail,
                caption_path=caption_path,
            )
        except Exception as exc:
            logger.warning("Upload package finalization failed: %s", exc)

    # Run the entire roadmap layer even when optional media intelligence is
    # unavailable. It emits deterministic sidecars rather than blocking a
    # successful render.
    try:
        from .content_factory import enrich_package
        enrich_package(pkg_dir, topic=topic, platform=platform)
    except Exception as exc:
        logger.warning("Content factory enrichment failed: %s", exc)

    return pkg_dir
