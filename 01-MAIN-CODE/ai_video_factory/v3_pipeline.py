"""Edit Factory v3 production wrapper with strict release contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .production_models import ProductionResult
from .production_pipeline import run_production_pipeline
from .scene_intelligence import analyze_video
from .v3_capabilities import validate_capabilities
from .v3_engine import V3Config, create_v3_blueprint, validate_blueprint
from .v3_quality import RenderContractError, enforce_retention_events, normalize_duration, strict_render_check


def _audience_profile(audience: str) -> Dict[str, Any]:
    """Turn the audience string into explicit planning preferences consumed by the script planner."""
    text = str(audience or "general short-form viewers").lower()
    profiles = [
        (("comedy", "funny", "humor", "meme"), {"tone": "playful", "pacing": "fast", "hook": "reaction_or_surprise", "caption_style": "punchy"}),
        (("anime", "manga", "otaku"), {"tone": "dramatic", "pacing": "fast", "hook": "character_or_reveal", "caption_style": "punchy"}),
        (("gaming", "gamer", "minecraft", "fortnite"), {"tone": "energetic", "pacing": "fast", "hook": "moment_or_payoff", "caption_style": "high_contrast"}),
        (("history", "documentary", "facts", "science", "education"), {"tone": "informative", "pacing": "measured", "hook": "evidence_or_question", "caption_style": "clear"}),
        (("business", "finance", "entrepreneur", "marketing"), {"tone": "direct", "pacing": "tight", "hook": "claim_or_result", "caption_style": "minimal"}),
    ]
    for markers, profile in profiles:
        if any(marker in text for marker in markers):
            return {"label": audience, **profile}
    return {"label": audience, "tone": "accessible", "pacing": "balanced", "hook": "curiosity_or_emotion", "caption_style": "readable"}


def _research_summary_from_blueprint(payload: Dict[str, Any], footage_evidence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    core = payload["core_idea"]
    best_hook = payload["hooks"][0] if payload.get("hooks") else {}
    clip_plan = payload.get("clip_plan", [])
    total_seconds = float(clip_plan[-1]["end"]) if clip_plan else 30.0
    avg_shot = total_seconds / max(1, len(clip_plan))
    profile = payload.get("platform_variants", {}).get(payload.get("platform", "youtube_shorts"), {})
    audience = payload.get("audience", "general short-form viewers")
    audience_profile = _audience_profile(audience)
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
        "audience": audience,
        "audience_profile": audience_profile,
        "platform_profile": profile,
        "footage_evidence": footage_evidence or {},
        "v3_directives": {
            "edit_type": payload["edit_type"],
            "clip_plan": clip_plan,
            "retention_map": payload.get("retention_map", []),
            "hooks": payload.get("hooks", []),
            "platform": payload.get("platform", "youtube_shorts"),
            "platform_profile": profile,
            "audience": audience,
            "audience_profile": audience_profile,
            "min_scene_match_score": 0.15,
            "disable_templates": True,
            "blueprint_contract": "3.0.0",
        },
    }


def _build_footage_evidence(input_video: str, enable_ocr: bool) -> Dict[str, Any]:
    """Analyze source footage before script generation so planning is footage-aware from the start."""
    scenes = analyze_video(input_video, sample_seconds=2.5, enable_ocr=enable_ocr)
    ranked = sorted(scenes, key=lambda scene: (scene.importance_score, scene.motion_score, scene.audio_energy), reverse=True)
    return {
        "scene_count": len(scenes),
        "top_scenes": [
            {
                "id": scene.id,
                "start": round(scene.start, 3),
                "end": round(scene.end, 3),
                "description": scene.description,
                "transcript": scene.transcript,
                "objects": scene.objects,
                "text": scene.text,
                "motion_score": round(scene.motion_score, 3),
                "audio_energy": round(scene.audio_energy, 3),
                "face_count": scene.face_count,
                "importance_score": round(scene.importance_score, 3),
            }
            for scene in ranked[:12]
        ],
    }


def _validate_timeline_contract(package: Path, blueprint_payload: Dict[str, Any], target_seconds: float) -> None:
    timeline_path = package / "timeline.json"
    if not timeline_path.exists():
        raise RenderContractError("V3 timeline artifact is missing")
    payload = json.loads(timeline_path.read_text(encoding="utf-8"))
    segments = payload.get("segments", [])
    clip_plan = blueprint_payload.get("clip_plan", [])
    if not isinstance(segments, list) or len(segments) != len(clip_plan):
        raise RenderContractError(
            f"render timeline segment count {len(segments) if isinstance(segments, list) else 0} != blueprint beat count {len(clip_plan)}"
        )
    for index, (segment, beat) in enumerate(zip(segments, clip_plan), start=1):
        if abs(float(segment.get("start", -1)) - float(beat["start"])) > 0.01:
            raise RenderContractError(f"timeline segment {index} start diverges from blueprint")
        if abs(float(segment.get("end", -1)) - float(beat["end"])) > 0.01:
            raise RenderContractError(f"timeline segment {index} end diverges from blueprint")
        if float(segment.get("end", 0)) <= float(segment.get("start", 0)):
            raise RenderContractError(f"timeline segment {index} has non-positive duration")
    if abs(float(payload.get("duration", 0.0)) - float(target_seconds)) > 0.01:
        raise RenderContractError("timeline duration diverges from V3 target")


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
    payload["audience"] = audience
    blueprint_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    if skip_qc and os.environ.get("AIVF_ALLOW_SKIP_QC") != "1":
        raise ValueError("skip_qc is disabled for strict v3 production; set AIVF_ALLOW_SKIP_QC=1 only for development")

    try:
        footage_evidence = _build_footage_evidence(input_video, enable_ocr)
    except Exception as exc:
        raise RenderContractError(f"pre-script footage analysis failed: {exc}") from exc

    result = run_production_pipeline(
        input_video,
        topic,
        package_dir,
        target_seconds=target_seconds,
        research_summary=_research_summary_from_blueprint(payload, footage_evidence),
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
            _validate_timeline_contract(package, payload, target_seconds)
            retention_path = str(package / "final.v3.retention.mp4")
            enforce_retention_events(result.final_video, retention_path, payload.get("retention_map", []))
            os.replace(retention_path, result.final_video)
            normalized_path = str(package / "final.v3.mp4")
            normalize_duration(result.final_video, normalized_path, target_seconds)
            os.replace(normalized_path, result.final_video)
            profile = payload["platform_variants"][platform]
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
                metadata["v3_timeline_contract"] = "passed"
                metadata["warnings"] = result.warnings
                metadata["errors"] = result.errors
                metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        except RenderContractError as exc:
            result.errors.append(f"V3 render contract failed: {exc}")

    result.artifacts = getattr(result, "artifacts", {}) or {}
    if isinstance(result.artifacts, dict):
        result.artifacts["v3_blueprint"] = str(blueprint_path)
        result.artifacts["v3_render_qc"] = str(package / "v3_render_qc.json")
        result.artifacts["v3_acceptance_matrix"] = "00-INFO/24-POINT-ACCEPTANCE.md"
    return result