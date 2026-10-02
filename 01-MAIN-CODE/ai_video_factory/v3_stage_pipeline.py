"""Stage-oriented V3 execution without changing the public pipeline API."""
from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

from .artifact_readiness import evaluate_artifact
from .audio_normalization import AudioNormalizationError, normalize_loudness
from .media_health import MediaHealthError, analyze_media
from .media_metadata import extract_media_metadata
from .metadata_guardrails import build_upload_metadata
from .production_assurance import (
    build_artifact_manifest,
    build_environment_fingerprint,
    build_release_evidence,
    verify_artifact_manifest,
)
from .production_guardrails import GuardrailError, atomic_write_json, require_free_disk, sha256_file
from .production_models import ProductionResult
from .provenance import (
    build_asset_record,
    load_provenance,
    manifest_needs_rights_review,
    provenance_manifest,
    write_provenance,
)
from .rights_policy import rights_gate
from .scene_intelligence import analyze_video, SceneAnalysisError
from .system_diagnostics import diagnostics_report, write_diagnostics
from .v3_asset_packager import V3AssetPackager
from .v3_contracts import V3Request
from .v3_engine import V3Blueprint, V3Config, create_v3_blueprint, validate_blueprint
from .v3_exceptions import (
    V3ArtifactError,
    V3ComplianceError,
    V3InputError,
    V3PackagingError,
    V3PipelineError,
    V3ValidationError,
)
from .v3_renderer_bridge import V3RenderPlan, V3RenderRequest, render_v3
from .v3_quality import RenderContractError, enforce_retention_events, normalize_duration, strict_render_check
from .v3_scores import V3ScoreBundle
from .v3_performance import analyze_stage_timings
from .v3_semantic_qc import analyze_render_semantics
from .render_engine import stamp_media_metadata


@dataclass
class V3ExecutionContext:
    request: V3Request
    source_metadata: dict[str, Any] | None
    package: Path
    result: ProductionResult = field(init=False)
    blueprint: V3Blueprint | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    blueprint_path: Path | None = None
    footage_evidence: dict[str, Any] = field(default_factory=dict)
    baseline_summary: dict[str, Any] = field(default_factory=dict)
    render_report: dict[str, Any] = field(default_factory=dict)
    semantic_report: dict[str, Any] = field(default_factory=dict)
    metadata_report: dict[str, Any] = field(default_factory=dict)
    source_health: dict[str, Any] = field(default_factory=dict)
    final_health: dict[str, Any] = field(default_factory=dict)
    final_media_metadata: dict[str, Any] = field(default_factory=dict)
    readiness: Any | None = None
    rights_report: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    environment_fingerprint: dict[str, Any] = field(default_factory=dict)
    artifact_integrity: dict[str, Any] = field(default_factory=dict)
    release_evidence: dict[str, Any] = field(default_factory=dict)
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    baseline_path: Path | None = None
    score_bundle: V3ScoreBundle | None = None

    def __post_init__(self) -> None:
        self.result = ProductionResult(package_dir=str(self.package))


class V3Stage(Protocol):
    name: str

    def run(self, context: V3ExecutionContext) -> None:
        ...


def _audience_profile(audience: str) -> dict[str, Any]:
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


def _research_summary(payload: dict[str, Any], footage_evidence: dict[str, Any]) -> dict[str, Any]:
    core = payload["core_idea"]
    best_hook = payload["hooks"][0] if payload.get("hooks") else {}
    clip_plan = payload.get("clip_plan", [])
    total_seconds = float(clip_plan[-1]["end"]) if clip_plan else 30.0
    average_shot = total_seconds / max(1, len(clip_plan))
    platform = str(payload.get("platform", "youtube_shorts"))
    audience = str(payload.get("audience", "general short-form viewers"))
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
        "avg_shot_duration": average_shot,
        "cuts_per_minute": round(60.0 / max(average_shot, 0.1), 2),
        "music_energy": 0.8 if payload["music"]["energy"] == "high" else 0.55,
        "music_style": payload["music"]["emotional_tone"],
        "v3_edit_type": payload["edit_type"],
        "v3_quality_score": payload["quality"]["score"],
        "v3_retention_score": payload["metrics"]["retention_score"],
        "thumbnail": payload.get("thumbnail_concept", ""),
        "platform": platform,
        "audience": audience,
        "audience_profile": _audience_profile(audience),
        "platform_profile": payload.get("platform_variants", {}).get(platform, {}),
        "footage_evidence": footage_evidence,
        "source_metadata": payload.get("source_metadata") or {},
        "v3_scores": payload.get("score_bundle") or {},
        "v3_directives": {
            "edit_type": payload["edit_type"],
            "clip_plan": clip_plan,
            "retention_map": payload.get("retention_map", []),
            "hooks": payload.get("hooks", []),
            "platform": platform,
            "platform_profile": payload.get("platform_variants", {}).get(platform, {}),
            "audience": audience,
            "audience_profile": _audience_profile(audience),
            "min_scene_match_score": 0.15,
            "disable_templates": True,
            "blueprint_contract": "3.0.0",
        },
    }


def _cleanup_transients(package: Path) -> None:
    for name in (
        "final.v3.retention.mp4",
        ".final.v3.normalized.mp4",
        ".final.v3.audio-normalized.mp4",
    ):
        (package / name).unlink(missing_ok=True)
    for child in package.glob(".aivf-*.partial"):
        child.unlink(missing_ok=True)
    for child in package.glob("aivf-v3-thumb-*"):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)


def _validate_timeline(package: Path, payload: dict[str, Any], target_seconds: float) -> None:
    path = package / "timeline.json"
    if not path.is_file():
        raise V3ArtifactError("V3 timeline artifact is missing")
    try:
        timeline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V3ArtifactError("V3 timeline artifact is not valid JSON") from exc
    segments = timeline.get("segments", [])
    beats = payload.get("clip_plan", [])
    if not isinstance(segments, list) or len(segments) != len(beats):
        raise V3ArtifactError("render timeline segment count does not match blueprint beat count")
    for index, (segment, beat) in enumerate(zip(segments, beats), start=1):
        try:
            start, end = float(segment["start"]), float(segment["end"])
            beat_start, beat_end = float(beat["start"]), float(beat["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise V3ArtifactError(f"timeline segment {index} contains invalid boundaries") from exc
        if abs(start - beat_start) > 0.01 or abs(end - beat_end) > 0.01:
            raise V3ValidationError(f"timeline segment {index} diverges from blueprint")
        if end <= start:
            raise V3ValidationError(f"timeline segment {index} has non-positive duration")
    try:
        duration = float(timeline["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise V3ArtifactError("timeline duration is invalid") from exc
    if abs(duration - float(target_seconds)) > 0.01:
        raise V3ValidationError("timeline duration diverges from V3 target")


class InputValidationStage:
    name = "input_validation"

    def run(self, context: V3ExecutionContext) -> None:
        from .v3_capabilities import validate_capabilities
        validate_capabilities()
        context.request.validate()
        environment = os.environ.get("AIVF_ENV", "production").strip().lower()
        override = os.environ.get("AIVF_ALLOW_SKIP_QC") == "1"
        if context.request.skip_qc and (environment not in {"development", "test"} or not override):
            raise V3InputError(
                "skip_qc is disabled for production; use development/test with AIVF_ALLOW_SKIP_QC=1"
            )
        semantic_disabled = os.environ.get("AIVF_V3_SEMANTIC_QC", "1") == "0"
        if semantic_disabled and (environment not in {"development", "test"} or not override):
            raise V3InputError(
                "AIVF_V3_SEMANTIC_QC=0 is allowed only in development/test with AIVF_ALLOW_SKIP_QC=1"
            )
        context.package.mkdir(parents=True, exist_ok=True)
        try:
            minimum_free_mb = int(os.environ.get("AIVF_MIN_FREE_DISK_MB", "512"))
        except ValueError as exc:
            raise V3InputError("AIVF_MIN_FREE_DISK_MB must be an integer") from exc
        if minimum_free_mb < 0:
            raise V3InputError("AIVF_MIN_FREE_DISK_MB cannot be negative")
        require_free_disk(context.package, minimum_free_mb * 1024 * 1024)
        _cleanup_transients(context.package)


class PlanningStage:
    name = "planning"

    def run(self, context: V3ExecutionContext) -> None:
        config = context.request.config()
        blueprint = create_v3_blueprint(
            context.request.topic,
            context=context.request.context,
            config=config,
            edit_type=context.request.edit_type,
        )
        validate_blueprint(blueprint)
        path = context.package / "v3_blueprint.json"
        payload = blueprint.to_dict()
        if context.source_metadata:
            payload["source_metadata"] = dict(context.source_metadata)
        atomic_write_json(path, payload)
        context.blueprint = V3Blueprint.from_dict(json.loads(path.read_text(encoding="utf-8")))
        context.blueprint_path = path
        context.payload = dict(context.blueprint.to_dict())
        if context.source_metadata:
            context.payload["source_metadata"] = dict(context.source_metadata)
        # Replacing the loaded blueprint's serialized score metadata is intentional:
        # the score contract is part of the V3 artifact and remains backward compatible.
        context.payload["score_bundle"] = context.blueprint.score_bundle.to_dict()


class SourceAnalysisStage:
    name = "source_analysis"

    def run(self, context: V3ExecutionContext) -> None:
        try:
            maximum = float(os.environ.get("AIVF_MAX_SOURCE_DURATION_SECONDS", "86400"))
        except ValueError as exc:
            raise V3InputError("AIVF_MAX_SOURCE_DURATION_SECONDS must be numeric") from exc
        if not math.isfinite(maximum) or maximum < 1800:
            raise V3InputError("AIVF_MAX_SOURCE_DURATION_SECONDS must be finite and >= 1800")

        try:
            context.source_health = analyze_media(
                context.request.input_video,
                deep=False,
                max_duration=maximum,
            )
        except MediaHealthError:
            raise
        atomic_write_json(context.package / "source_media_health.json", context.source_health)

        try:
            metadata = extract_media_metadata(context.request.input_video)
        except (OSError, RuntimeError, ValueError) as exc:
            metadata = {"ok": False, "error": str(exc)}
        atomic_write_json(context.package / "source_media_metadata.json", metadata)

        try:
            scenes = analyze_video(
                context.request.input_video,
                sample_seconds=2.5,
                min_scenes=len(context.blueprint.clip_plan) if context.blueprint else 0,
                enable_ocr=context.request.enable_ocr,
            )
        except SceneAnalysisError:
            raise
        ranked = sorted(
            scenes,
            key=lambda scene: (scene.importance_score, scene.motion_score, scene.audio_energy),
            reverse=True,
        )
        context.footage_evidence = {
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


class RenderStage:
    name = "render"

    def run(self, context: V3ExecutionContext) -> None:
        if context.blueprint is None:
            raise V3ArtifactError("planning stage did not produce a blueprint")
        context.baseline_summary = _research_summary(context.payload | {"retention_map": []}, context.footage_evidence)
        request = V3RenderRequest(
            input_video=context.request.input_video,
            topic=context.request.topic,
            package_dir=str(context.package),
            target_seconds=context.request.target_seconds,
            research_summary=context.baseline_summary,
            model_key=context.request.model_key,
            render_plan=V3RenderPlan.from_blueprint(context.blueprint, include_retention=False),
            skip_qc=context.request.skip_qc,
            music_path=context.request.music_path,
            enable_ocr=context.request.enable_ocr,
            enable_object_detection=context.request.enable_object_detection,
            enable_diarization=context.request.enable_diarization,
            diarization_token=context.request.diarization_token,
            platform=context.request.platform,
        )
        context.result = render_v3(request)


class MediaValidationStage:
    name = "media_validation"

    def run(self, context: V3ExecutionContext) -> None:
        if context.result.errors or not context.result.final_video:
            return
        _validate_timeline(context.package, context.payload, context.request.target_seconds)
        baseline = context.package / "v3_baseline.mp4"
        retention = context.package / "final.v3.retention.mp4"
        normalized = context.package / ".final.v3.normalized.mp4"
        canonical = context.package / "final.v3.mp4"

        shutil.copyfile(context.result.final_video, baseline)
        enforce_retention_events(
            context.result.final_video,
            str(retention),
            context.payload.get("retention_map", []),
        )
        normalize_duration(str(retention), str(normalized), context.request.target_seconds)
        profile = context.payload["platform_variants"][context.request.platform]
        report = strict_render_check(
            str(normalized),
            target_seconds=context.request.target_seconds,
            platform_profile=profile,
            retention_events=context.payload.get("retention_map", []),
            retention_baseline=str(baseline),
            require_independent_retention=True,
        )
        if not report["ok"]:
            raise RenderContractError(
                "; ".join(str(error) for error in report.get("errors", [])) or "V3 render QC failed"
            )
        os.replace(normalized, canonical)
        context.result.final_video = str(canonical)
        context.baseline_path = baseline
        context.render_report = report

        source = Path(context.result.final_video)
        if os.environ.get("AIVF_EBU_R128", "1").strip() != "0":
            normalized_audio = context.package / ".final.v3.audio-normalized.mp4"
            try:
                media = analyze_media(source, deep=False, max_duration=3600.0)
                if media["summary"].get("has_audio"):
                    normalize_loudness(source, normalized_audio)
                    os.replace(normalized_audio, source)
            except (AudioNormalizationError, MediaHealthError, OSError, ValueError) as exc:
                normalized_audio.unlink(missing_ok=True)
                raise RenderContractError(f"EBU R128 final audio normalization failed: {exc}") from exc

        try:
            context.result.final_video = stamp_media_metadata(
                context.result.final_video,
                version=os.environ.get("AIVF_VERSION", "3.0.0"),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            context.result.warnings.append(f"Final metadata stamping skipped: {exc}")

        try:
            context.final_health = analyze_media(
                context.result.final_video,
                deep=os.environ.get("AIVF_DEEP_FINAL_MEDIA_QC", "1").strip() != "0",
            )
        except (MediaHealthError, GuardrailError, OSError, ValueError) as exc:
            context.final_health = {
                "ok": False,
                "errors": [str(exc)],
                "warnings": [],
                "defects": [str(exc)],
            }
        atomic_write_json(context.package / "final_media_health.json", context.final_health)

        try:
            context.final_media_metadata = extract_media_metadata(context.result.final_video)
        except (OSError, RuntimeError, ValueError) as exc:
            context.final_media_metadata = {"ok": False, "error": str(exc)}
        atomic_write_json(context.package / "final_media_metadata.json", context.final_media_metadata)

        if not context.final_health.get("ok", False):
            defects = list(context.final_health.get("defects") or context.final_health.get("errors") or [])
            context.result.errors.extend(
                f"V3 final media health: {defect}"
                for defect in (defects or ["technical media health check failed"])
            )

        context.semantic_report = {"ok": True, "mode": "disabled"}
        if os.environ.get("AIVF_V3_SEMANTIC_QC", "1") != "0":
            context.semantic_report = analyze_render_semantics(context.result.final_video)
            if not context.semantic_report["ok"]:
                context.result.errors.extend(
                    "V3 semantic QC: " + error
                    for error in context.semantic_report["errors"]
                )
            context.result.warnings.extend(
                "V3 semantic QC: " + warning
                for warning in context.semantic_report["warnings"]
            )

        technical = 100.0 if context.render_report.get("ok") and context.final_health.get("ok") else 0.0
        if context.blueprint is not None:
            context.score_bundle = context.blueprint.score_bundle.final_with_render(
                technical_validity=technical
            )
            atomic_write_json(
                context.package / "v3_scores.json",
                context.score_bundle.to_dict(),
            )


class PackagingStage:
    name = "packaging"

    def run(self, context: V3ExecutionContext) -> None:
        if context.result.errors or not context.result.final_video:
            return
        V3AssetPackager().package(
            result=context.result,
            package=context.package,
            topic=context.request.topic,
            platform=context.request.platform,
            source_video=context.request.input_video,
            baseline_summary=context.baseline_summary,
        )


class ComplianceStage:
    name = "compliance"

    def run(self, context: V3ExecutionContext) -> None:
        if context.result.errors or not context.result.final_video:
            return

        source_meta = context.source_metadata or {}
        context.metadata_report = build_upload_metadata(
            context.request.topic,
            summary=context.baseline_summary,
            hook=str(context.baseline_summary.get("hook") or ""),
            attribution=str(source_meta.get("attribution") or ""),
        )
        atomic_write_json(context.package / "metadata_guardrails.json", context.metadata_report)
        metadata_ok = bool(context.metadata_report.get("quality", {}).get("ok")) and not bool(
            (context.metadata_report.get("factuality") or {}).get("publish_blocked")
        )
        if not metadata_ok:
            errors = context.metadata_report.get("quality", {}).get("errors") or []
            context.result.errors.extend(f"Metadata guardrail: {error}" for error in errors)

        context.readiness = evaluate_artifact(
            context.result.final_video,
            target_seconds=context.request.target_seconds,
            platform_profile=context.payload["platform_variants"][context.request.platform],
            package_dir=str(context.package),
            upload_package_required=True,
            publish_required=False,
            metadata_guardrails_ok=metadata_ok,
        )
        if context.readiness.errors:
            context.result.errors.extend(
                f"V3 artifact readiness: {error}" for error in context.readiness.errors
            )

        rights_status = str(source_meta.get("rights_status") or "review_required").strip().lower()
        rights_record = {
            "asset_id": "source_video",
            "source": "user_upload",
            "rights_basis": rights_status,
            "evidence_url": str(source_meta.get("evidence_url") or ""),
            "license_url": str(source_meta.get("license_url") or ""),
            "declared_by": str(source_meta.get("declared_by") or ""),
            "declared_at": str(source_meta.get("declared_at") or ""),
            "attribution": str(source_meta.get("attribution") or ""),
        }
        context.rights_report = rights_gate([rights_record], strict=True)

        if context.blueprint_path is None:
            raise V3ArtifactError("blueprint path missing before provenance generation")
        source_record = build_asset_record(
            context.request.input_video,
            asset_id="source_video",
            source="user_upload",
            source_url=str(source_meta.get("source_url") or ""),
            rights_status=rights_status,
            license_name=str(source_meta.get("license_name") or ""),
            license_url=str(source_meta.get("license_url") or ""),
            attribution=str(source_meta.get("attribution") or ""),
            extra={"rights_evidence": rights_record},
        )
        context.provenance = provenance_manifest(
            context.package,
            final_video=context.result.final_video,
            assets=[source_record],
            pipeline_version="3.0.0",
            run_context={
                "blueprint_sha256": sha256_file(context.blueprint_path),
                "platform": context.request.platform,
                "target_seconds": round(float(context.request.target_seconds), 6),
                "edit_type": context.payload.get("edit_type"),
            },
        )
        context.provenance["rights_gate"] = context.rights_report
        write_provenance(context.package / "provenance.json", context.provenance)
        if manifest_needs_rights_review(context.provenance):
            context.result.warnings.append("Media provenance contains assets requiring rights review")

        context.diagnostics = diagnostics_report(directories=[context.package])
        write_diagnostics(context.package / "diagnostics.json", context.diagnostics)

        context.environment_fingerprint = build_environment_fingerprint(
            pipeline_version="3.0.0",
            platform_name=context.request.platform,
            target_seconds=context.request.target_seconds,
            platform_profile=context.payload["platform_variants"][context.request.platform],
        )
        atomic_write_json(
            context.package / "environment_fingerprint.json",
            context.environment_fingerprint,
        )


class ReleaseEvidenceStage:
    name = "release_evidence"

    def run(self, context: V3ExecutionContext) -> None:
        if context.result.errors or not context.result.final_video:
            return
        if context.readiness is None:
            raise V3ArtifactError("readiness was not evaluated before release evidence")
        self._persist_reports(context)

        context.readiness = evaluate_artifact(
            context.result.final_video,
            target_seconds=context.request.target_seconds,
            platform_profile=context.payload["platform_variants"][context.request.platform],
            package_dir=str(context.package),
            upload_package_required=True,
            publish_required=False,
            metadata_guardrails_ok=bool(
                context.metadata_report.get("quality", {}).get("ok")
            )
            and not bool((context.metadata_report.get("factuality") or {}).get("publish_blocked")),
        )
        if context.readiness.errors:
            context.result.errors.extend(
                f"V3 artifact readiness: {error}"
                for error in context.readiness.errors
            )
        atomic_write_json(context.package / "v3_readiness.json", context.readiness.to_dict())
        self._persist_reports(context)

        manifest = build_artifact_manifest(
            context.package,
            required_files=(
                "final.v3.mp4",
                "v3_blueprint.json",
                "timeline.json",
                "v3_render_qc.json",
                "v3_readiness.json",
                "upload_package.json",
                "provenance.json",
                "metadata_guardrails.json",
                "diagnostics.json",
                "environment_fingerprint.json",
            ),
        )
        atomic_write_json(context.package / "artifact_manifest.json", manifest)
        context.artifact_integrity = verify_artifact_manifest(context.package, manifest)
        if not context.artifact_integrity.get("ok"):
            context.result.errors.extend(
                f"Artifact integrity: {error}"
                for error in list(context.artifact_integrity.get("errors") or [])[:10]
            )

        context.release_evidence = build_release_evidence(
            package_dir=context.package,
            readiness=context.readiness.to_dict(),
            media_health=context.final_health,
            provenance=context.provenance,
            environment={**context.diagnostics, **context.environment_fingerprint},
            artifact_integrity=context.artifact_integrity,
        )
        atomic_write_json(context.package / "release_evidence.json", context.release_evidence)
        if not context.release_evidence.get("release_candidate"):
            context.result.warnings.append(
                "Automated release evidence is incomplete; human review remains required"
            )

    @staticmethod
    def _persist_reports(context: V3ExecutionContext) -> None:
        atomic_write_json(context.package / "v3_render_qc.json", context.render_report)
        atomic_write_json(context.package / "v3_semantic_qc.json", context.semantic_report)
        if context.readiness is not None:
            atomic_write_json(context.package / "v3_readiness.json", context.readiness.to_dict())
        metadata_path = context.package / "metadata.json"
        if metadata_path.is_file():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                metadata = {}
            metadata.update(
                {
                    "v3_render_qc": context.render_report,
                    "v3_semantic_qc": context.semantic_report,
                    "v3_readiness": context.readiness.to_dict() if context.readiness is not None else {},
                    "v3_timeline_contract": "passed",
                    "v3_scores": context.score_bundle.to_dict() if context.score_bundle else {},
                    "warnings": context.result.warnings,
                    "errors": context.result.errors,
                }
            )
            atomic_write_json(metadata_path, metadata)


class V3PipelineRunner:
    """Execute the V3 production contract as independent, timed stages."""

    def __init__(
        self,
        request: V3Request,
        *,
        source_metadata: dict[str, Any] | None = None,
        stages: Iterable[V3Stage] | None = None,
    ) -> None:
        self.request = request
        self.source_metadata = dict(source_metadata or {}) or None
        self.stages = tuple(stages or (
            InputValidationStage(),
            PlanningStage(),
            SourceAnalysisStage(),
            RenderStage(),
            MediaValidationStage(),
            PackagingStage(),
            ComplianceStage(),
            ReleaseEvidenceStage(),
        ))

    def run(self) -> ProductionResult:
        context = V3ExecutionContext(
            request=self.request,
            source_metadata=self.source_metadata,
            package=Path(self.request.package_dir),
        )
        for stage in self.stages:
            started = time.perf_counter()
            try:
                stage.run(context)
            except V3PipelineError as exc:
                context.result.errors.append(f"[{stage.name}] {exc}")
                break
            finally:
                context.stage_timings_ms[stage.name] = round(
                    (time.perf_counter() - started) * 1000.0,
                    3,
                )

            if context.result.errors:
                break

        if context.package.exists():
            performance = analyze_stage_timings(context.stage_timings_ms)
            atomic_write_json(
                context.package / "v3_stage_timings.json",
                {
                    "stages_ms": context.stage_timings_ms,
                    "total_ms": performance.total_ms,
                    "bottleneck": performance.bottleneck,
                    "stages": [
                        {
                            "name": item.name,
                            "milliseconds": item.milliseconds,
                            "share": item.share,
                        }
                        for item in performance.stages
                    ],
                },
            )

            # A failed stage must still leave a machine-readable failure record.
            # Do not fabricate successful media/provenance evidence; only record
            # what is actually known at the point of failure.
            if context.result.errors:
                atomic_write_json(
                    context.package / "v3_failure.json",
                    {
                        "ok": False,
                        "errors": list(context.result.errors),
                        "stage_timings_ms": dict(context.stage_timings_ms),
                    },
                )
                if not (context.package / "v3_readiness.json").is_file():
                    atomic_write_json(
                        context.package / "v3_readiness.json",
                        {
                            "state": "FAILED",
                            "checks": {
                                "MEDIA_VALID": False,
                                "MEDIA_CONTRACT_VALID": False,
                                "UPLOAD_PACKAGE_VALID": False,
                                "PUBLISH_READY": False,
                            },
                            "errors": list(context.result.errors),
                            "warnings": [],
                        },
                    )

        artifact_names = (
            "v3_blueprint",
            "v3_render_qc",
            "v3_semantic_qc",
            "v3_readiness",
            "v3_baseline",
            "source_media_health",
            "source_media_metadata",
            "final_media_health",
            "final_media_metadata",
            "provenance",
            "metadata_guardrails",
            "diagnostics",
            "environment_fingerprint",
            "artifact_manifest",
            "release_evidence",
            "v3_scores",
            "v3_stage_timings",
            "v3_failure",
        )
        context.result.artifacts.update(
            {
                name: str(context.package / f"{name}.json")
                if name not in {"v3_baseline"}
                else str(context.package / "v3_baseline.mp4")
                for name in artifact_names
                if (context.package / (
                    "v3_baseline.mp4" if name == "v3_baseline" else f"{name}.json"
                )).is_file()
            }
        )
        _cleanup_transients(context.package)
        return context.result


__all__ = [
    "ComplianceStage",
    "InputValidationStage",
    "MediaValidationStage",
    "PackagingStage",
    "PlanningStage",
    "ReleaseEvidenceStage",
    "RenderStage",
    "SourceAnalysisStage",
    "V3ExecutionContext",
    "V3PipelineRunner",
    "V3Stage",
]
