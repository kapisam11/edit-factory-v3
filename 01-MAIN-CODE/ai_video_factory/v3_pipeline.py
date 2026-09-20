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
from .v3_renderer_bridge import V3RenderRequest, render_v3
from .v3_capabilities import validate_capabilities
from .v3_engine import V3Config, create_v3_blueprint, validate_blueprint
from .v3_quality import RenderContractError, enforce_retention_events, normalize_duration, strict_render_check
from .v3_semantic_qc import analyze_render_semantics


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
        "v3_directives": {
            "edit_type": payload["edit_type"], "clip_plan": clip_plan, "retention_map": payload.get("retention_map", []),
            "hooks": payload.get("hooks", []), "platform": payload.get("platform", "youtube_shorts"), "platform_profile": profile,
            "audience": audience, "audience_profile": audience_profile, "min_scene_match_score": 0.15,
            "disable_templates": True, "blueprint_contract": "3.0.0",
        },
    }


def _build_footage_evidence(input_video: str, enable_ocr: bool) -> Dict[str, Any]:
    scenes = analyze_video(input_video, sample_seconds=2.5, enable_ocr=enable_ocr)
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
        raise RenderContractError("timeline duration diverges from V3 target")


def _atomic_json_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".aivf-", suffix=".partial", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try: os.unlink(temp_path)
        except OSError: pass
        raise


def _validate_v3_inputs(input_video: str, topic: str, target_seconds: float, platform: str, audience: str, bpm: int) -> None:
    path = Path(input_video)
    if not path.is_file():
        raise RenderContractError(f"V3 input video is missing or not a file: {path}")
    if path.stat().st_size <= 0:
        raise RenderContractError("V3 input video is empty")
    if not str(topic).strip() or len(str(topic)) > 500:
        raise ValueError("topic must be non-empty and <= 500 characters")
    try:
        target = float(target_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("target_seconds must be numeric") from exc
    if not math.isfinite(target) or not 8.0 <= target <= 180.0:
        raise ValueError("target_seconds must be between 8 and 180 seconds")
    if not str(platform).strip() or len(str(platform)) > 64:
        raise ValueError("platform must be non-empty and <= 64 characters")
    if not str(audience).strip() or len(str(audience)) > 500:
        raise ValueError("audience must be non-empty and <= 500 characters")
    try:
        bpm_value = int(bpm)
    except (TypeError, ValueError) as exc:
        raise ValueError("bpm must be an integer") from exc
    if not 40 <= bpm_value <= 240:
        raise ValueError("bpm must be between 40 and 240")


def run_v3_pipeline(input_video: str, topic: str, package_dir: str, *, context: str = "", target_seconds: float = 30.0,
                    platform: str = "youtube_shorts", audience: str = "general short-form viewers", bpm: int = 120,
                    edit_type: Optional[str] = None, model_key: Optional[str] = None, skip_qc: bool = False,
                    music_path: Optional[str] = None, enable_ocr: bool = False, enable_object_detection: bool = True,
                    enable_diarization: bool = False, diarization_token: Optional[str] = None) -> ProductionResult:
    validate_capabilities()
    _validate_v3_inputs(input_video, topic, target_seconds, platform, audience, bpm)
    config = V3Config(target_seconds=float(target_seconds), platform=platform, audience=audience, bpm=int(bpm))
    blueprint = create_v3_blueprint(topic, context=context, config=config, edit_type=edit_type)
    validate_blueprint(blueprint)

    package = Path(package_dir)
    package.mkdir(parents=True, exist_ok=True)
    blueprint_path = package / "v3_blueprint.json"
    payload = blueprint.to_dict(); payload["platform"] = platform; payload["audience"] = audience
    _atomic_json_write(blueprint_path, payload)

    if skip_qc and os.environ.get("AIVF_ALLOW_SKIP_QC") != "1":
        raise ValueError("skip_qc is disabled for strict v3 production; set AIVF_ALLOW_SKIP_QC=1 only for development")
    try:
        footage_evidence = _build_footage_evidence(input_video, enable_ocr)
    except Exception as exc:
        raise RenderContractError(f"pre-script footage analysis failed: {exc}") from exc

    # Render the contract once with retention directives removed. This is the
    # independent baseline; retention effects are added only after this render.
    baseline_payload = dict(payload)
    baseline_payload["retention_map"] = []
    baseline_summary = _research_summary_from_blueprint(baseline_payload, footage_evidence)
    request = V3RenderRequest(
        input_video=input_video,
        topic=topic,
        package_dir=package_dir,
        target_seconds=target_seconds,
        research_summary=baseline_summary,
        model_key=model_key,
        skip_qc=skip_qc,
        music_path=music_path,
        enable_ocr=enable_ocr,
        enable_object_detection=enable_object_detection,
        enable_diarization=enable_diarization,
        diarization_token=diarization_token,
        platform=platform,
    )
    result = render_v3(request)

    if result.final_video and not result.errors:
        try:
            _validate_timeline_contract(package, payload, target_seconds)
            baseline_path = package / "v3_baseline.mp4"
            shutil.copyfile(result.final_video, baseline_path)
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
                retention_baseline=str(baseline_path),
                require_independent_retention=True,
            )
            semantic_report = {"ok": True, "mode": "disabled"}
            if os.environ.get("AIVF_V3_SEMANTIC_QC", "1") != "0":
                semantic_report = analyze_render_semantics(result.final_video)
                if not semantic_report["ok"]:
                    result.errors.extend(
                        "V3 semantic QC: " + error for error in semantic_report["errors"]
                    )
                result.warnings.extend(
                    "V3 semantic QC: " + warning for warning in semantic_report["warnings"]
                )
            try:
                from .upload_package import finalize_upload_package
                upload_manifest = finalize_upload_package(
                    package_dir,
                    topic=topic,
                    summary=baseline_summary,
                    script=str(baseline_summary.get("script", "")),
                    platform=platform,
                    final_video=result.final_video,
                    thumbnail=str(baseline_summary.get("thumbnail") or "") or None,
                    caption_path=str(package / "captions.ass") if (package / "captions.ass").exists() else None,
                )
                result.artifacts["upload_package_manifest"] = str(package / "upload_package.json")
            except Exception as exc:
                upload_manifest = None
                result.errors.append(f"V3 upload package finalization failed: {exc}")

            readiness = evaluate_artifact(
                result.final_video,
                target_seconds=target_seconds,
                platform_profile=profile,
                package_dir=package_dir,
                upload_package_required=True,
                publish_required=False,
            )
            _atomic_json_write(package / "v3_render_qc.json", render_report)
            _atomic_json_write(package / "v3_semantic_qc.json", semantic_report)
            _atomic_json_write(package / "v3_readiness.json", readiness.to_dict())
            if not render_report["ok"]:
                result.errors.extend("V3 render QC: " + error for error in render_report["errors"])
            result.warnings.extend("V3 render QC: " + warning for warning in render_report["warnings"])
            if readiness.state != "MEDIA_CONTRACT_VALID":
                result.warnings.append(
                    f"V3 artifact readiness stopped at {readiness.state}; upload/publish readiness is not claimed"
                )
            metadata_path = package / "metadata.json"
            if metadata_path.exists():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["v3_render_qc"] = render_report
                metadata["v3_semantic_qc"] = semantic_report
                metadata["v3_readiness"] = readiness.to_dict()
                metadata["v3_timeline_contract"] = "passed"
                metadata["warnings"] = result.warnings
                metadata["errors"] = result.errors
                _atomic_json_write(metadata_path, metadata)
        except RenderContractError as exc:
            result.errors.append(f"V3 render contract failed: {exc}")

    result.artifacts = getattr(result, "artifacts", {}) or {}
    if isinstance(result.artifacts, dict):
        result.artifacts["v3_blueprint"] = str(blueprint_path)
        result.artifacts["v3_render_qc"] = str(package / "v3_render_qc.json")
        result.artifacts["v3_semantic_qc"] = str(package / "v3_semantic_qc.json")
        result.artifacts["v3_readiness"] = str(package / "v3_readiness.json")
        result.artifacts["v3_baseline"] = str(package / "v3_baseline.mp4")
        result.artifacts["v3_renderer_bridge"] = "ai_video_factory.v3_renderer_bridge"
        result.artifacts["v3_acceptance_matrix"] = "00-INFO/24-POINT-ACCEPTANCE.md"
    return result
