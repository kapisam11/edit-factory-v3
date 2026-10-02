"""Edit Factory v3 production wrapper with strict release contracts."""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .artifact_readiness import evaluate_artifact
from .production_models import ProductionResult
from .scene_intelligence import analyze_video
from .v3_renderer_bridge import V3RenderPlan, V3RenderRequest, render_v3
from .v3_capabilities import validate_capabilities
from .v3_engine import V3Blueprint, V3Config, create_v3_blueprint, validate_blueprint
from .v3_quality import RenderContractError, enforce_retention_events, normalize_duration, strict_render_check
from .v3_semantic_qc import analyze_render_semantics
from .v3_contracts import V3Request
from .v3_exceptions import V3PipelineError
from .v3_stage_pipeline import V3PipelineRunner
from .media_health import MediaHealthError, analyze_media
from .media_metadata import extract_media_metadata
from .audio_normalization import AudioNormalizationError, normalize_loudness
from .render_engine import stamp_media_metadata
from .metadata_guardrails import build_upload_metadata
from .provenance import build_asset_record, load_provenance, manifest_needs_rights_review, provenance_manifest, write_provenance
from .system_diagnostics import diagnostics_report, write_diagnostics
from .production_guardrails import GuardrailError, atomic_write_json, require_free_disk, sha256_file
from .production_assurance import (
    build_artifact_manifest,
    build_environment_fingerprint,
    build_release_evidence,
)


def _audience_profile(audience: str) -> Dict[str, Any]:
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
        "topic": core["topic"], "emotion": core["target_emotion"], "strongest_angle": core["emotional_angle"],
        "main_conflict": core["stakes"], "why_care": core["why_people_care"], "watch_to_end_reason": core["watch_to_end_reason"],
        "payoff": core["payoff"], "hook": best_hook.get("text", ""), "visual_hook": best_hook.get("visual", ""),
        "target_total_seconds": total_seconds, "hook_duration": clip_plan[0]["end"] if clip_plan else 2.0,
        "avg_shot_duration": avg_shot, "cuts_per_minute": round(60.0 / max(avg_shot, 0.1), 2),
        "music_energy": 0.8 if payload["music"]["energy"] == "high" else 0.55,
        "music_style": payload["music"]["emotional_tone"], "v3_edit_type": payload["edit_type"],
        "v3_quality_score": payload["quality"]["score"], "v3_retention_score": payload["metrics"]["retention_score"],
        "thumbnail": payload.get("thumbnail_concept", ""), "platform": payload.get("platform", "youtube_shorts"),
        "audience": audience, "audience_profile": audience_profile, "platform_profile": profile,
        "footage_evidence": footage_evidence or {},
        "source_metadata": payload.get("source_metadata") or {},
        "v3_directives": {
            "edit_type": payload["edit_type"], "clip_plan": clip_plan, "retention_map": payload.get("retention_map", []),
            "hooks": payload.get("hooks", []), "platform": payload.get("platform", "youtube_shorts"), "platform_profile": profile,
            "audience": audience, "audience_profile": audience_profile, "min_scene_match_score": 0.15,
            "disable_templates": True, "blueprint_contract": "3.0.0",
        },
    }


def _build_footage_evidence(input_video: str, enable_ocr: bool, min_scenes: int = 0) -> Dict[str, Any]:
    scenes = analyze_video(
        input_video,
        sample_seconds=2.5,
        min_scenes=max(0, int(min_scenes)),
        enable_ocr=enable_ocr,
    )
    ranked = sorted(scenes, key=lambda scene: (scene.importance_score, scene.motion_score, scene.audio_energy), reverse=True)
    return {"scene_count": len(scenes), "top_scenes": [{
        "id": scene.id, "start": round(scene.start, 3), "end": round(scene.end, 3), "description": scene.description,
        "transcript": scene.transcript, "objects": scene.objects, "text": scene.text,
        "motion_score": round(scene.motion_score, 3), "audio_energy": round(scene.audio_energy, 3),
        "face_count": scene.face_count, "importance_score": round(scene.importance_score, 3),
    } for scene in ranked[:12]]}


def _validate_timeline_contract(package: Path, blueprint_payload: Dict[str, Any], target_seconds: float) -> None:
    timeline_path = package / "timeline.json"
    if not timeline_path.is_file():
        raise RenderContractError("V3 timeline artifact is missing")
    payload = json.loads(timeline_path.read_text(encoding="utf-8"))
    segments = payload.get("segments", [])
    clip_plan = blueprint_payload.get("clip_plan", [])
    if not isinstance(segments, list) or len(segments) != len(clip_plan):
        raise RenderContractError(f"render timeline segment count {len(segments) if isinstance(segments, list) else 0} != blueprint beat count {len(clip_plan)}")
    for index, (segment, beat) in enumerate(zip(segments, clip_plan), start=1):
        try:
            start = float(segment["start"]); end = float(segment["end"])
            beat_start = float(beat["start"]); beat_end = float(beat["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RenderContractError(f"timeline segment {index} has invalid numeric boundaries") from exc
        if abs(start - beat_start) > 0.01 or abs(end - beat_end) > 0.01:
            raise RenderContractError(f"timeline segment {index} diverges from blueprint")
        if end <= start:
            raise RenderContractError(f"timeline segment {index} has non-positive duration")
    try:
        timeline_duration = float(payload["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RenderContractError("timeline duration is invalid") from exc
    if abs(timeline_duration - float(target_seconds)) > 0.01:
        raise RenderContractError("timeline duration divergesdef _validate_v3_inputs(
    input_video: str,
    topic: str,
    target_seconds: float,
    platform: str,
    audience: str,
    bpm: int,
) -> None:
    """Compatibility wrapper around the canonical V3Request validator."""
    from .v3_contracts import V3Request
    from .v3_exceptions import V3InputError

    try:
        V3Request(
            input_video=input_video,
            topic=topic,
            package_dir=".",
            target_seconds=target_seconds,
            platform=platform,
            audience=audience,
            bpm=bpm,
        ).validate()
    except V3InputError as exc:
        raise RenderContractError(str(exc)) from exc


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
    source_metadata: Optional[Dict[str, Any]] = None,
) -> ProductionResult:
    """Run the public V3 API through the stage-oriented production runner."""
    request = V3Request(
        input_video=input_video,
        topic=topic,
        package_dir=package_dir,
        context=context,
        target_seconds=target_seconds,
        platform=platform,
        audience=audience,
        bpm=bpm,
        edit_type=edit_type,
        model_key=model_key,
        skip_qc=skip_qc,
        music_path=music_path,
        enable_ocr=enable_ocr,
        enable_object_detection=enable_object_detection,
        enable_diarization=enable_diarization,
        diarization_token=diarization_token,
    )
    try:
        return V3PipelineRunner(
            request,
            source_metadata=source_metadata,
        ).run()
    except V3PipelineError:
        raise

