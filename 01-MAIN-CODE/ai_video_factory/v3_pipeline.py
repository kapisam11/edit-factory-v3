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
from .rights_policy import rights_gate
from .system_diagnostics import diagnostics_report, write_diagnostics
from .production_guardrails import GuardrailError, atomic_write_json, require_free_disk, sha256_file
from .production_assurance import (
    build_artifact_manifest,
    build_environment_fingerprint,
    build_release_evidence,
    verify_artifact_manifest,
)


def _source_rights_status(source_metadata: Optional[Dict[str, Any]]) -> str:
    """Return only an explicit rights status; rights_basis never grants approval."""
    metadata = source_metadata if isinstance(source_metadata, dict) else {}
    return str(metadata.get("rights_status") or "review_required").strip().lower()


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
        raise RenderContractError("timeline duration diverges from V3 target")


def _atomic_json_write(path: Path, payload: Dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def _validate_v3_inputs(
    input_video: str,
    topic: str,
    target_seconds: float,
    platform: str,
    audience: str,
    bpm: int,
) -> None:
    """Compatibility wrapper around the canonical V3Request validator."""
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
    except V3PipelineError as exc:
        raise RenderContractError(str(exc)) from exc


def _normalize_final_audio(package: Path, final_video: str) -> str:
    """Normalize final-program audio while preserving the video stream."""
    if os.environ.get("AIVF_EBU_R128", "1").strip() == "0":
        return final_video
    source = Path(final_video)
    normalized = package / ".final.v3.audio-normalized.mp4"
    try:
        media = analyze_media(source, deep=False, max_duration=3600.0)
        if not media["summary"].get("has_audio"):
            return final_video
        normalize_loudness(source, normalized)
        if not normalized.is_file() or normalized.stat().st_size <= 0:
            raise AudioNormalizationError("normalized final audio artifact is missing")
        os.replace(normalized, source)
        return str(source)
    except (AudioNormalizationError, MediaHealthError, OSError, ValueError) as exc:
        normalized.unlink(missing_ok=True)
        raise RenderContractError(f"EBU R128 final audio normalization failed: {exc}") from exc


def _cleanup_v3_transients(package: Path) -> None:
    """Remove only known V3 temporary artifacts; preserve canonical outputs for inspection."""
    for name in ("final.v3.retention.mp4", ".final.v3.normalized.mp4", ".final.v3.audio-normalized.mp4"):
        (package / name).unlink(missing_ok=True)
    for child in package.glob(".aivf-*.partial"):
        child.unlink(missing_ok=True)
    for child in package.glob("aivf-v3-thumb-*"):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


def _prepare_blueprint(
    topic: str, context: str, target_seconds: float, platform: str, audience: str, bpm: int,
    package: Path, edit_type: Optional[str], source_metadata: Optional[Dict[str, Any]],
) -> tuple[V3Blueprint, Dict[str, Any], Path]:
    config = V3Config(target_seconds=float(target_seconds), platform=platform, audience=audience, bpm=int(bpm))
    blueprint = create_v3_blueprint(topic, context=context, config=config, edit_type=edit_type)
    validate_blueprint(blueprint)
    blueprint_path = package / "v3_blueprint.json"
    payload = blueprint.to_dict()
    if source_metadata:
        payload["source_metadata"] = dict(source_metadata)
    _atomic_json_write(blueprint_path, payload)
    persisted = V3Blueprint.from_dict(json.loads(blueprint_path.read_text(encoding="utf-8")))
    persisted_payload = persisted.to_dict()
    if source_metadata:
        persisted_payload["source_metadata"] = dict(source_metadata)
    return persisted, persisted_payload, blueprint_path


def _build_baseline_request(
    *, input_video: str, topic: str, package_dir: str, target_seconds: float, platform: str,
    blueprint: V3Blueprint, blueprint_payload: Dict[str, Any], footage_evidence: Dict[str, Any], model_key: Optional[str],
    skip_qc: bool, music_path: Optional[str], enable_ocr: bool, enable_object_detection: bool,
    enable_diarization: bool, diarization_token: Optional[str],
) -> V3RenderRequest:
    baseline_payload = dict(blueprint_payload)
    baseline_payload["retention_map"] = []
    summary = _research_summary_from_blueprint(baseline_payload, footage_evidence)
    return V3RenderRequest(
        input_video=input_video,
        topic=topic,
        package_dir=package_dir,
        target_seconds=target_seconds,
        research_summary=summary,
        model_key=model_key,
        render_plan=V3RenderPlan.from_blueprint(blueprint, include_retention=False),
        skip_qc=skip_qc,
        music_path=music_path,
        enable_ocr=enable_ocr,
        enable_object_detection=enable_object_detection,
        enable_diarization=enable_diarization,
        diarization_token=diarization_token,
        platform=platform,
    )


def _finalize_v3_media(
    result: ProductionResult, package: Path, payload: Dict[str, Any], target_seconds: float,
) -> tuple[str, Path, Dict[str, Any]]:
    _validate_timeline_contract(package, payload, target_seconds)
    if not result.final_video:
        raise RenderContractError("renderer returned no final video")
    baseline_path = package / "v3_baseline.mp4"
    retention_path = package / "final.v3.retention.mp4"
    normalized_path = package / ".final.v3.normalized.mp4"
    canonical_final = package / payload.get("packaging", {}).get("final_video_name", "final.v3.mp4")
    try:
        shutil.copyfile(result.final_video, baseline_path)
        enforce_retention_events(result.final_video, str(retention_path), payload.get("retention_map", []))
        normalize_duration(str(retention_path), str(normalized_path), target_seconds)
        profile = payload["platform_variants"][payload["platform"]]
        report = strict_render_check(
            str(normalized_path),
            target_seconds=target_seconds,
            platform_profile=profile,
            retention_events=payload.get("retention_map", []),
            retention_baseline=str(baseline_path),
            require_independent_retention=True,
        )
        if not report["ok"]:
            raise RenderContractError("; ".join(str(error) for error in report.get("errors", [])) or "V3 render QC failed")
        os.replace(normalized_path, canonical_final)
        result.final_video = str(canonical_final)
        return str(canonical_final), baseline_path, report
    except Exception:
        retention_path.unlink(missing_ok=True)
        normalized_path.unlink(missing_ok=True)
        raise


def _package_v3_assets(
    *, result: ProductionResult, package: Path, topic: str, platform: str, source_video: str, baseline_summary: Dict[str, Any],
) -> None:
    try:
        from .thumbnail import extract_best_video_frame, make_thumbnail_variants, make_thumbnail_vertical, select_best_thumbnail_variant
        thumb_dir = package / "thumbnails"
        thumb_dir.mkdir(parents=True, exist_ok=True)
        thumbnail_work_dir = tempfile.mkdtemp(prefix="aivf-v3-thumb-", dir=str(package))
        try:
            frame_path = extract_best_video_frame(source_video, thumbnail_work_dir)
            thumbnail_subject = str(baseline_summary.get("hook") or baseline_summary.get("thumbnail") or topic).strip()
            variants = make_thumbnail_variants(
                thumbnail_subject, str(thumb_dir), count=3, topic=topic, background_path=frame_path
            )
            if not variants:
                raise RuntimeError("thumbnail generation returned no variants")
            selected_variant = select_best_thumbnail_variant(variants)
            thumbnail_path = package / "thumbnail.png"
            shutil.copyfile(variants[selected_variant - 1], thumbnail_path)
            make_thumbnail_vertical(
                thumbnail_subject, str(package / "thumbnail_vertical.png"),
                size=(1080, 1920), background_path=frame_path,
            )
            baseline_summary["thumbnail"] = str(thumbnail_path)
            result.artifacts["thumbnail"] = str(thumbnail_path)
            result.artifacts["thumbnail_variants"] = str(thumb_dir)
        finally:
            shutil.rmtree(thumbnail_work_dir, ignore_errors=True)
        from .upload_package import finalize_upload_package
        finalize_upload_package(
            str(package),
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
        result.errors.append(f"V3 upload package finalization failed: {exc}")


def _persist_v3_reports(
    package: Path, result: ProductionResult, render_report: Dict[str, Any],
    semantic_report: Dict[str, Any], readiness: Any,
) -> None:
    _atomic_json_write(package / "v3_render_qc.json", render_report)
    _atomic_json_write(package / "v3_semantic_qc.json", semantic_report)
    _atomic_json_write(package / "v3_readiness.json", readiness.to_dict())
    metadata_path = package / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.update({
            "v3_render_qc": render_report,
            "v3_semantic_qc": semantic_report,
            "v3_readiness": readiness.to_dict(),
            "v3_timeline_contract": "passed",
            "warnings": result.warnings,
            "errors": result.errors,
        })
        _atomic_json_write(metadata_path, metadata)


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
    """Run V3 through the explicit production stages.

    The legacy function signature remains stable for CLI/dashboard callers.
    """
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
    return V3PipelineRunner(request, source_metadata=source_metadata).run()

