"""Edit Factory v3 production wrapper.

This module makes the v3 creative blueprint a first-class input to the single
production renderer instead of treating the blueprint as reporting metadata.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .production_models import ProductionResult
from .production_pipeline import run_production_pipeline
from .v3_engine import V3Config, create_v3_blueprint, validate_blueprint


def _research_summary_from_blueprint(payload: Dict[str, Any]) -> Dict[str, Any]:
    core = payload["core_idea"]
    best_hook = payload["hooks"][0] if payload.get("hooks") else {}
    clip_plan = payload.get("clip_plan", [])
    total_seconds = clip_plan[-1]["end"] if clip_plan else 30.0
    avg_shot = total_seconds / max(1, len(clip_plan))
    profile = payload.get("platform_variants", {}).get(payload.get("platform", "youtube_shorts"), {})
    return {
        "topic": core["topic"],
        "emotion": core["target_emotion"],
        "strongest_angle": core["emotional_angle"],
        "main_conflict": core["stakes"],
        "why_care": core["why_people_care"],
        "watch_to_end_reason": core["watch_to_end_reason"],
        "payoff": core["payoff"],
        "hook": best_hook.get("text", ""),
        "visual_hook": best_hook.get("visual", ""),
        "target_total_seconds": total_seconds,
        "hook_duration": clip_plan[0]["end"] if clip_plan else 2.0,
        "avg_shot_duration": avg_shot,
        "cuts_per_minute": round(60.0 / max(avg_shot, 0.1), 2),
        "music_energy": 0.8 if payload["music"]["energy"] == "high" else 0.55,
        "music_style": payload["music"]["emotional_tone"],
        "v3_edit_type": payload["edit_type"],
        "v3_quality_score": payload["quality"]["score"],
        "v3_retention_score": payload["metrics"]["retention_score"],
        "thumbnail": payload.get("thumbnail_concept", ""),
        "platform": payload.get("platform", "youtube_shorts"),
        "platform_profile": profile,
        "v3_directives": {
            "edit_type": payload["edit_type"],
            "clip_plan": clip_plan,
            "retention_map": payload.get("retention_map", []),
            "hooks": payload.get("hooks", []),
            "platform": payload.get("platform", "youtube_shorts"),
            "platform_profile": profile,
        },
    }


def run_v3_pipeline(
    input_video: str,
    topic: str,
    package_dir: str,
    *,
    context: str = "",
    target_seconds: float = 30.0,
    platform: str = "youtube_shorts",
    audience: str = "general short-form viewers",
    bpm: int = 120,
    edit_type: Optional[str] = None,
    model_key: Optional[str] = None,
    skip_qc: bool = False,
    music_path: Optional[str] = None,
    enable_ocr: bool = False,
    enable_object_detection: bool = True,
    enable_diarization: bool = False,
    diarization_token: Optional[str] = None,
) -> ProductionResult:
    """Plan emotion first, persist the blueprint, then execute the shared renderer."""
    config = V3Config(
        target_seconds=target_seconds,
        platform=platform,
        audience=audience,
        bpm=bpm,
    )
    blueprint = create_v3_blueprint(topic, context=context, config=config, edit_type=edit_type)
    validate_blueprint(blueprint)

    package = Path(package_dir)
    package.mkdir(parents=True, exist_ok=True)
    blueprint_path = package / "v3_blueprint.json"
    payload = blueprint.to_dict()
    payload["platform"] = platform
    blueprint_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    result = run_production_pipeline(
        input_video,
        topic,
        package_dir,
        target_seconds=target_seconds,
        research_summary=_research_summary_from_blueprint(payload),
        enable_ocr=enable_ocr,
        model_key=model_key,
        skip_qc=skip_qc,
        music_path=music_path,
        enable_object_detection=enable_object_detection,
        enable_diarization=enable_diarization,
        diarization_token=diarization_token,
        platform=platform,
    )
    result.artifacts = getattr(result, "artifacts", {}) or {}
    if isinstance(result.artifacts, dict):
        result.artifacts["v3_blueprint"] = str(blueprint_path)
    return result
