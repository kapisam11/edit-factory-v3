"""Edit Factory v3 production wrapper with strict release contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .production_models import ProductionResult
from .production_pipeline import run_production_pipeline
from .v3_capabilities import validate_capabilities
from .v3_engine import V3Config, create_v3_blueprint, validate_blueprint
from .v3_quality import RenderContractError, normalize_duration, strict_render_check


def _research_summary_from_blueprint(payload: Dict[str, Any]) -> Dict[str, Any]:
    core = payload["core_idea"]
    best_hook = payload["hooks"][0] if payload.get("hooks") else {}
    clip_plan = payload.get("clip_plan", [])
    total_seconds = float(clip_plan[-1]["end"]) if clip_plan else 30.0
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
            "min_scene_match_score": 0.05,
            "disable_templates": True,
            "blueprint_contract": "3.0.0",
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
    validate_capabilities()
    config = V3Config(target_seconds=target_seconds, platform=platform, audience=audience, bpm=bpm)
    blueprint = create_v3_blueprint(topic, context=context, config=config, edit_type=edit_type)
    validate_blueprint(blueprint)

    package = Path(package_dir)
    package.mkdir(parents=True, exist_ok=True)
    blueprint_path = package / "v3_blueprint.json"
    payload = blueprint.to_dict()
    payload["platform"] = platform
    blueprint_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    if skip_qc and os.environ.get("AIVF_ALLOW_SKIP_QC") != "1":
        raise ValueError("skip_qc is disabled for strict v3 production; set AIVF_ALLOW_SKIP_QC=1 only for development")

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

    if result.final_video and not result.errors:
        try:
            profile = payload["platform_variants"][platform]
            normalized_path = str(package / "final.v3.mp4")
            normalize_duration(result.final_video, normalized_path, target_seconds)
            os.replace(normalized_path, result.final_video)
            render_report = strict_render_check(
                result.final_video,
                target_seconds=target_seconds,
                platform_profile=profile,
                retention_events=payload.get("retention_map", []),
            )
            (package / "v3_render_qc.json").write_text(
                json.dumps(render_report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            if not render_report["ok"]:
                result.errors.extend("V3 render QC: " + error for error in render_report["errors"])
            result.warnings.extend("V3 render QC: " + warning for warning in render_report["warnings"])
            metadata_path = package / "metadata.json"
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["v3_render_qc"] = render_report
                metadata["warnings"] = result.warnings
                metadata["errors"] = result.errors
                metadata_path.write_text(
                    json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
                )
        except RenderContractError as exc:
            result.errors.append(f"V3 render contract failed: {exc}")

    result.artifacts = getattr(result, "artifacts", {}) or {}
    if isinstance(result.artifacts, dict):
        result.artifacts["v3_blueprint"] = str(blueprint_path)
        result.artifacts["v3_render_qc"] = str(package / "v3_render_qc.json")
        result.artifacts["v3_acceptance_matrix"] = "00-INFO/24-POINT-ACCEPTANCE.md"
    return result
