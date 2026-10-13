"""Stage-oriented V3 execution without changing the public pipeline API."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .artifact_readiness import evaluate_artifact
from .audio_normalization import AudioNormalizationError, normalize_loudness
from .media_health import MediaHealthError, analyze_media
from .media_metadata import extract_media_metadata
from .metadata_guardrails import build_upload_metadata, validate_metadata
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
from .scene_intelligence import analyze_video, save_scene_index, SceneAnalysisError
from .system_diagnostics import diagnostics_report, write_diagnostics
from .v3_asset_packager import V3AssetPackager
from .v3_contracts import V3Request
from .v3_engine import V3Blueprint, V3Config, create_v3_blueprint, validate_blueprint
from .v3_exceptions import (
    V3ArtifactError,
    V3ComplianceError,
    V3ConfigurationError,
    V3InputError,
    V3PackagingError,
    V3PipelineError,
    V3ValidationError,
)
from .v3_renderer_bridge import V3RenderPlan, V3RenderRequest, render_v3
from .v3_quality import RenderContractError, enforce_retention_events, normalize_duration, probe_media, strict_render_check
from .v3_scores import V3ScoreBundle
from .v3_performance import analyze_stage_timings
from .editorial_evaluation import summarize_editorial_evidence
from .idempotency import file_hash, stage_cache_key, cache_record
from .v3_semantic_qc import analyze_render_semantics
from .render_engine import stamp_media_metadata
from .v3.source_manifest import build_source_manifest
from .v3.workspace import WorkspaceBusyError, WorkspaceLock
from .v3.job_identity import job_identity, configuration_hash
from .v3.creative_provenance import build_creative_provenance
from .v3.final_content_manifest import build_final_content_manifest
from .v3.metadata import generate_final_metadata
from .v3.clip_evidence import build_clip_evidence
from .v3.audience import parse_audience
from .v3.platform_policy import get_platform_policy


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
    job_id: str = ""
    source_manifest: dict[str, Any] = field(default_factory=dict)
    creative_provenance: dict[str, Any] = field(default_factory=dict)
    baseline_path: Path | None = None
    score_bundle: V3ScoreBundle | None = None
    stage_cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.result = ProductionResult(package_dir=str(self.package))


class V3Stage(Protocol):
    name: str

    def run(self, context: V3ExecutionContext) -> None:
        ...


def _audience_profile(audience: str) -> dict[str, Any]:
    return parse_audience(audience).to_dict()


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
        "v3_retention_heuristic": payload["metrics"].get("retention_heuristic", payload["metrics"].get("retention_score", 0.0)),
        "thumbnail": payload.get("thumbnail_concept", ""),
        "platform": platform,
        "audience": audience,
        "audience_profile": _audience_profile(audience),
        "platform_profile": payload.get("platform_variants", {}).get(platform, {}),
        "footage_evidence": footage_evidence,

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


_V3_GENERATED_FILES = frozenset({
    "final.v3.mp4",
    "final.mp4",
    "timeline.json",
    "metadata.json",
    "upload_package.json",
    "v3_blueprint.json",
    "v3_render_qc.json",
    "v3_semantic_qc.json",
    "v3_readiness.json",
    "v3_baseline.mp4",
    "source_media_health.json",
    "source_media_metadata.json",
    "scenes.json",
    "final_media_health.json",
    "final_media_metadata.json",
    "provenance.json",
    "metadata_guardrails.json",
    "diagnostics.json",
    "environment_fingerprint.json",
    "artifact_manifest.json",
    "release_evidence.json",
    "v3_scores.json",
    "v3_stage_timings.json",
    "v3_failure.json",
    "v3_stage_cache.json",
    "editorial_decisions.json",
    "source_manifest.json",
    "creative_provenance.json",
    "job_identity.json",
    "v3_cumulative_metrics.json",
    "v3_performance.json",
    "source_manifest.json",
    "creative_provenance.json",
    "job_identity.json",
    "final_content_manifest.json",
    "clip_source_evidence.json",
})
_V3_TRANSIENT_FILE_NAMES = (
    "final.v3.retention.mp4",
    ".final.v3.normalized.mp4",
    ".final.v3.audio-normalized.mp4",
)




def _request_identity(
    request: V3Request,
    *,
    source_metadata: Mapping[str, Any] | None = None,
) -> str:
    music_path = request.music_path
    music_fingerprint: str | None = None
    if music_path:
        try:
            music_fingerprint = file_hash(music_path)
        except (OSError, ValueError):
            music_fingerprint = str(music_path)
    diarization_token_hash = (
        configuration_hash({"diarization_token": request.diarization_token})
        if request.diarization_token
        else ""
    )
    return configuration_hash({
        "topic": request.topic,
        "context": request.context,
        "target_seconds": request.target_seconds,
        "platform": request.platform,
        "audience": request.audience,
        "bpm": request.bpm,
        "edit_type": request.edit_type,
        "model_key": request.model_key,
        "skip_qc": request.skip_qc,
        "music_fingerprint": music_fingerprint,
        "enable_ocr": request.enable_ocr,
        "enable_object_detection": request.enable_object_detection,
        "enable_diarization": request.enable_diarization,
        "allow_unsupported_critical_evidence": request.allow_unsupported_critical_evidence,
        "diarization_token_hash": diarization_token_hash,
        "source_metadata": dict(source_metadata or {}),
    })


def _expected_job_identity(
    request: V3Request,
    *,
    source_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_hash = file_hash(request.input_video)
    config = request.config()
    config_hash = configuration_hash(asdict(config))
    platform_policy_version = get_platform_policy(request.platform).version
    return {
        "job_id": "",
        "request_hash": _request_identity(request, source_metadata=source_metadata),
        "source_hash": source_hash,
        "blueprint_hash": "",
        "configuration_hash": config_hash,
        "renderer_version": "3.0.0",
        "platform_policy_version": platform_policy_version,
    }


_WORKSPACE_MARKERS = frozenset({
    "v3_blueprint.json", "final.v3.mp4", "job_identity.json", "v3_readiness.json",
    "artifact_manifest.json", "upload_package.json", "final_content_manifest.json",
})


def _guard_workspace_isolation(
    request: V3Request,
    *,
    source_metadata: Mapping[str, Any] | None = None,
) -> None:
    package = Path(request.package_dir)
    if not package.exists():
        return
    markers = [package / name for name in _WORKSPACE_MARKERS if (package / name).exists()]
    if not markers:
        return
    identity = package / "job_identity.json"
    expected = _expected_job_identity(request, source_metadata=source_metadata)
    if not identity.is_file():
        readiness_path = package / "v3_readiness.json"
        failure_path = package / "v3_failure.json"
        if failure_path.is_file():
            # A failed attempt is explicitly retryable; the next run may safely reset
            # generated outputs because no successful identity exists to protect.
            return
        try:
            readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            readiness = {}
        if readiness.get("state") == "FAILED":
            return
        raise V3InputError(
            "package directory already contains generated artifacts without a job identity; "
            "use a dedicated jobs/<job_id> workspace"
        )
    try:
        stored = json.loads(identity.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V3InputError("package directory has an unreadable job identity; refusing destructive reuse") from exc
    comparable = (
        stored.get("request_hash") == expected["request_hash"]
        and stored.get("source_hash") == expected["source_hash"]
        and stored.get("configuration_hash") == expected["configuration_hash"]
        and stored.get("renderer_version") == expected["renderer_version"]
        and stored.get("platform_policy_version") == expected["platform_policy_version"]
    )
    if not comparable:
        raise V3InputError(
            "package directory belongs to a different V3 job; refusing to mix artifacts"
        )


def _reuse_existing_job(
    request: V3Request,
    *,
    source_metadata: Mapping[str, Any] | None = None,
) -> ProductionResult | None:
    package = Path(request.package_dir)
    identity_path = package / "job_identity.json"
    final_path = package / "final.v3.mp4"
    readiness_path = package / "v3_readiness.json"
    manifest_path = package / "artifact_manifest.json"
    if not all(path.is_file() for path in (identity_path, final_path, readiness_path, manifest_path)):
        return None
    try:
        stored = json.loads(identity_path.read_text(encoding="utf-8"))
        expected = _expected_job_identity(request, source_metadata=source_metadata)
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None

    expected_job = job_identity(
        source_hash=expected["source_hash"],
        blueprint_hash=str(stored.get("blueprint_hash") or ""),
        config_hash=expected["configuration_hash"],
        renderer_version=expected["renderer_version"],
        platform_policy_version=expected["platform_policy_version"],
    )
    comparable = (
        stored.get("request_hash") == expected["request_hash"]
        and stored.get("source_hash") == expected["source_hash"]
        and stored.get("configuration_hash") == expected["configuration_hash"]
        and stored.get("renderer_version") == expected["renderer_version"]
        and stored.get("platform_policy_version") == expected["platform_policy_version"]
        and stored.get("job_id") == expected_job
        and bool(stored.get("blueprint_hash"))
    )
    if not comparable or readiness.get("state") not in {"UPLOAD_PACKAGE_VALID", "PUBLISH_READY"}:
        return None

    from .production_assurance import verify_artifact_manifest
    integrity = verify_artifact_manifest(package, manifest, verify_hashes=True)
    if not integrity.get("ok"):
        return None
    final_entries = [
        item for item in (manifest.get("files") or [])
        if isinstance(item, Mapping) and item.get("path") == "final.v3.mp4"
    ]
    if len(final_entries) != 1:
        return None
    recorded_hash = str(final_entries[0].get("sha256") or "")
    try:
        if not recorded_hash or file_hash(final_path) != recorded_hash:
            return None
        from .v3_quality import probe_media
        media = probe_media(str(final_path))
    except (OSError, ValueError, RenderContractError):
        return None

    policy = get_platform_policy(request.platform)
    if (
        abs(float(media["duration"]) - float(request.target_seconds)) > 0.08
        or int(media["width"]) != int(policy.width)
        or int(media["height"]) != int(policy.height)
        or not os.path.getsize(final_path) > 0
    ):
        return None

    result = ProductionResult(package_dir=str(package))
    result.final_video = str(final_path)
    result.artifacts = {
        file_path.stem: str(file_path)
        for file_path in package.glob("*.json")
        if file_path.is_file()
    }
    result.artifacts["final_video"] = str(final_path)
    result.warnings.append(
        "Reused existing verified V3 job artifact via content-addressed job identity"
    )
    return result


def _write_failed_evidence(
    package: Path,
    errors: list[str],
    stage_timings_ms: dict[str, float] | None = None,
) -> None:
    package.mkdir(parents=True, exist_ok=True)
    timings = dict(stage_timings_ms or {})
    atomic_write_json(
        package / "v3_failure.json",
        {
            "ok": False,
            "errors": list(errors),
            "stage_timings_ms": timings,
        },
    )
    atomic_write_json(
        package / "v3_readiness.json",
        {
            "state": "FAILED",
            "checks": {
                "MEDIA_VALID": False,
                "MEDIA_CONTRACT_VALID": False,
                "UPLOAD_PACKAGE_VALID": False,
                "PUBLISH_READY": False,
            },
            "errors": list(errors),
            "warnings": [],
        },
    )

def _reset_v3_package(
    package: Path,
    *,
    input_video: str | None = None,
) -> None:
    """Remove stale V3 outputs without following package-entry symlinks."""
    package.mkdir(parents=True, exist_ok=True)
    package_entry = package.absolute()
    protected_input = Path(input_video).absolute() if input_video else None
    protected_name: str | None = None

    if protected_input is not None:
        try:
            relative_input = protected_input.relative_to(package_entry)
        except ValueError:
            pass
        else:
            # Compare the lexical package entry, not resolve(), so a symlink
            # source is protected too and can never be followed into its target.
            if relative_input.name in _V3_GENERATED_FILES:
                protected_name = relative_input.name
                stale_paths = [package / name for name in _V3_GENERATED_FILES if name != protected_name]
                for target in stale_paths:
                    target.unlink(missing_ok=True)
                _cleanup_transients(package)
                _write_failed_evidence(
                    package,
                    [
                        "V3 input video cannot use a generated package filename inside package_dir: "
                        + protected_name
                    ],
                )
                raise V3InputError(
                    "V3 input video cannot use a generated package filename inside package_dir: "
                    + protected_name
                )

    for name in _V3_GENERATED_FILES:
        if name == protected_name:
            continue
        (package / name).unlink(missing_ok=True)
    _cleanup_transients(package)

def _cleanup_transients(package: Path, *, preserve: bool = False) -> None:
    if preserve:
        return
    for name in (
        "final.v3.retention.mp4",
        ".final.v3.normalized.mp4",
        ".final.v3.audio-normalized.mp4",
    ):
        (package / name).unlink(missing_ok=True)
    for child in package.glob(".aivf-*.partial"):
        child.unlink(missing_ok=True)
    for child in package.glob("aivf-v3-thumb-*"):
        if child.is_symlink():
            child.unlink(missing_ok=True)
        elif child.is_dir():
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
        try:
            validate_capabilities()
        except (TypeError, ValueError) as exc:
            raise V3ConfigurationError(
                f"V3 capability validation failed: {exc}"
            ) from exc
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
        if not 256 <= minimum_free_mb <= 1024 * 1024:
            raise V3InputError(
                "AIVF_MIN_FREE_DISK_MB must be between 256 and 1048576"
            )
        require_free_disk(context.package, minimum_free_mb * 1024 * 1024)
        _cleanup_transients(context.package)


class PlanningStage:
    name = "planning"

    def run(self, context: V3ExecutionContext) -> None:
        config = context.request.config()
        fixture_evidence_override = (
            os.environ.get("AIVF_ENV", "").strip().lower() == "test"
            and context.request.allow_unsupported_critical_evidence
        )
        planning_footage_evidence = (
            None
            if fixture_evidence_override
            else context.footage_evidence
        )
        blueprint = create_v3_blueprint(
            context.request.topic,
            context=context.request.context,
            config=config,
            edit_type=context.request.edit_type,
            footage_evidence=planning_footage_evidence,
        )
        validate_blueprint(blueprint)
        path = context.package / "v3_blueprint.json"
        payload = blueprint.to_dict()
        atomic_write_json(path, payload)
        context.blueprint = V3Blueprint.from_dict(json.loads(path.read_text(encoding="utf-8")))
        context.blueprint_path = path
        context.payload = dict(context.blueprint.to_dict())
        context.payload["score_bundle"] = context.blueprint.score_bundle.to_dict()

        source_manifest = build_source_manifest(
            context.request.input_video,
            context.source_metadata or {},
        )
        context.source_manifest = source_manifest.to_dict()
        atomic_write_json(context.package / "source_manifest.json", context.source_manifest)

        source_hash = source_manifest.source_sha256
        config_payload = asdict(config)
        cfg_hash = configuration_hash(config_payload)
        bp_hash = configuration_hash(context.payload)
        context.job_id = job_identity(
            source_hash=source_hash,
            blueprint_hash=bp_hash,
            config_hash=cfg_hash,
            renderer_version="3.0.0",
            platform_policy_version=blueprint.platform_constraints.policy_version,
        )
        atomic_write_json(
            context.package / "job_identity.json",
            {
                "job_id": context.job_id,
                "request_hash": _request_identity(context.request, source_metadata=context.source_metadata),
                "source_hash": source_hash,
                "blueprint_hash": bp_hash,
                "configuration_hash": cfg_hash,
                "renderer_version": "3.0.0",
                "platform_policy_version": blueprint.platform_constraints.policy_version,
            },
        )
        provenance = build_creative_provenance(
            pipeline_version="3.0.0",
            planner_version="3.0.0",
            retention_policy_version="2.0.0-semantic",
            caption_policy_version="3.0.0",
            scoring_version="3.0.0",
            prompt_template_version="3.0.0",
            model_version=context.request.model_key or "deterministic",
            configuration=config_payload,
            source_hash=source_hash,
            blueprint_payload=context.payload,
            renderer_version="3.0.0",
            platform_policy_version=blueprint.platform_constraints.policy_version,
        )
        context.creative_provenance = provenance.to_dict()
        clip_evidence = build_clip_evidence(
            [dict(item) for item in context.payload.get("clip_plan", [])],
            context.footage_evidence,
            source_asset=str(context.request.input_video),
        )
        atomic_write_json(context.package / "clip_source_evidence.json", clip_evidence)
        unsupported_critical = [
            item for item in clip_evidence
            if item.get("purpose") in {"Hook", "Payoff", "Punchline", "Climax", "Final impact"}
            and item.get("status") != "supported"
        ]
        if unsupported_critical:
            allow_ci_fixture = context.request.allow_unsupported_critical_evidence
            if not allow_ci_fixture:
                raise V3ValidationError(
                    "critical editorial beats have no supporting source evidence: "
                    + ", ".join(str(item.get("clip_index")) for item in unsupported_critical)
                )
            atomic_write_json(
                context.package / "v3_source_evidence_warning.json",
                {
                    "warning": "Synthetic/test run allowed critical source evidence gaps",
                    "clip_indices": [item.get("clip_index") for item in unsupported_critical],
                    "policy": "production default remains strict",
                },
            )
            context.result.warnings.append(
                "Synthetic/test override allowed unsupported critical source evidence"
            )

        atomic_write_json(context.package / "creative_provenance.json", context.creative_provenance)

        atomic_write_json(
            context.package / "editorial_decisions.json",
            {
                "version": "2.0.0",
                "decisions": [item.to_dict() for item in context.blueprint.editorial_decisions],
                "summary": summarize_editorial_evidence(context.blueprint.editorial_decisions),
            },
        )


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

        config = context.request.config()
        requested_scene_count = max(
            6,
            min(24, int(math.ceil(config.target_seconds / 2.7))),
        )
        try:
            scenes = analyze_video(
                context.request.input_video,
                sample_seconds=2.5,
                min_scenes=requested_scene_count,
                enable_ocr=context.request.enable_ocr,
            )
        except SceneAnalysisError:
            raise
        try:
            save_scene_index(
                scenes,
                str(context.package / "scenes.json"),
                source_video=str(context.request.input_video),
            )
        except (OSError, IOError, RuntimeError, ValueError) as exc:
            raise V3PipelineError(f"scene index persistence failed: {exc}") from exc
        ranked = sorted(
            scenes,
            key=lambda scene: (scene.importance_score, scene.motion_score, scene.audio_energy),
            reverse=True,
        )
        context.footage_evidence = {
            "source_asset": str(context.request.input_video),
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
        profile = context.payload["platform_variants"][context.request.platform]
        enforce_retention_events(
            context.result.final_video,
            str(retention),
            context.payload.get("retention_map", []),
            target_seconds=context.request.target_seconds,
            normalize_audio=os.environ.get("AIVF_EBU_R128", "1").strip() != "0",
            frame_rate_fps=float(profile.get("frame_rate_fps") or 30.0),
        )
        report = strict_render_check(
            str(retention),
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
        os.replace(retention, canonical)
        context.result.final_video = str(canonical)
        context.baseline_path = baseline
        context.render_report = report

        try:
            context.result.final_video = stamp_media_metadata(
                context.result.final_video,
                version=os.environ.get("AIVF_VERSION", "3.0.0"),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            context.result.warnings.append(f"Final metadata stamping skipped: {exc}")

        # Revalidate the exact promoted artifact after the metadata remux. The
        # pre-promotion report alone is not evidence about bytes written afterward.
        post_stamp_report = strict_render_check(
            str(context.result.final_video),
            target_seconds=context.request.target_seconds,
            platform_profile=profile,
            retention_events=context.payload.get("retention_map", []),
            retention_baseline=str(baseline),
            require_independent_retention=True,
        )
        context.render_report["post_metadata_stamp_validation"] = post_stamp_report
        context.render_report["ok"] = bool(context.render_report.get("ok") and post_stamp_report.get("ok"))
        if not post_stamp_report.get("ok"):
            errors = list(post_stamp_report.get("errors") or [])
            raise RenderContractError(
                "post-metadata-stamp final media validation failed: "
                + ("; ".join(str(error) for error in errors) if errors else "media contract returned not-ok")
            )

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


def _editorial_corpus_path(context: V3ExecutionContext) -> Path:
    """Keep the mutable empirical corpus outside immutable release artifacts."""
    configured = os.environ.get("AIVF_EDITORIAL_CORPUS_PATH")
    if configured:
        return Path(configured)
    return context.package.parent / "editorial_corpus.sqlite"


class EvaluationCaptureStage:
    """Capture the produced job in the empirical corpus without fabricating labels."""

    name = "evaluation_capture"

    def run(self, context: V3ExecutionContext) -> None:
        if context.result.errors or not context.result.final_video:
            return
        from .editorial_corpus import CorpusCase, EditorialCorpus

        corpus_path = _editorial_corpus_path(context)
        case = CorpusCase(
            case_id=context.job_id,
            video_id=context.job_id,
            blueprint_version=str(context.payload.get("version", "3.0.0")),
            renderer_version=str(
                context.creative_provenance.get("renderer_version", "3.0.0")
            ),
            metadata={
                "final_video": str(context.result.final_video),
                "edit_type": context.payload.get("edit_type", ""),
                "platform": context.request.platform,
                "final_content_manifest": context.baseline_summary.get(
                    "content_manifest", {}
                ),
                "final_metadata": context.baseline_summary.get("final_metadata", {}),
                "editorial_prediction_method": "deterministic blueprint QC heuristics",
                "editorial_prediction_dimensions": [
                    "hook", "pacing", "coherence", "caption_quality", "payoff", "overall"
                ],
            },
        )
        corpus = EditorialCorpus(corpus_path)
        corpus.add_case(case)
        quality_checks = context.payload.get("quality", {}).get("checks", {})
        def check_score(*names: str) -> float:
            values = [1.0 if bool(quality_checks.get(name)) else 0.0 for name in names if name in quality_checks]
            return round(sum(values) / len(values), 4) if values else 0.0

        # These are deterministic editorial predictions derived from the blueprint's
        # own QC evidence. They are explicitly predictions, never human labels.
        editorial_predictions = {
            "hook": check_score("hook_under_two_seconds", "hook_context_gap"),
            "pacing": check_score("duration_bounds", "exact_duration", "retention_semantic_coverage"),
            "coherence": check_score("single_edit_type_strategy", "every_clip_has_purpose", "hook_context_gap"),
            "caption_quality": check_score("overlay_word_limit", "no_duplicate_overlays"),
            "payoff": check_score("has_payoff", "has_final_impact"),
        }
        editorial_predictions["overall"] = round(
            sum(editorial_predictions.values()) / len(editorial_predictions), 4
        )
        prediction_metrics = {
            **dict(context.payload.get("metrics", {})),
            **{name: value * 100.0 for name, value in editorial_predictions.items()},
        }
        corpus.add_blueprint_predictions(
            context.job_id,
            prediction_metrics,
            confidence=float(
                context.blueprint.score_bundle.creative_quality_confidence
                if context.blueprint is not None
                else 0.0
            ),
        )
        report = corpus.performance_report()
        report["correlation"] = corpus.correlation_report()
        report["case_id"] = context.job_id
        report["labels_present"] = False
        report["label_policy"] = (
            "Human annotations and platform outcomes must be supplied explicitly; "
            "the pipeline never invents them."
        )
        atomic_write_json(context.package / "v3_evaluation_report.json", report)



def _prepare_final_content_metadata(context: V3ExecutionContext) -> dict[str, Any]:
    """Build authoritative content/metadata artifacts before upload packaging."""
    content_manifest = build_final_content_manifest(
        topic=context.request.topic,
        blueprint=context.payload,
        footage_evidence=context.footage_evidence,
        final_media_metadata=context.final_media_metadata,
        source_manifest=context.source_manifest,
    )
    final_metadata = generate_final_metadata(
        content_manifest,
        source_manifest=context.source_manifest,
    )
    atomic_write_json(context.package / "final_content_manifest.json", content_manifest)
    atomic_write_json(context.package / "final_metadata.json", final_metadata)
    summary = dict(context.baseline_summary)
    summary.update(
        {
            "content_manifest": content_manifest,
            "final_metadata": final_metadata,
            "source_manifest": context.source_manifest,
        }
    )
    context.baseline_summary = summary
    return final_metadata


class PackagingStage:
    name = "packaging"

    def run(self, context: V3ExecutionContext) -> None:
        if context.result.errors or not context.result.final_video:
            return
        # Packaging deliberately precedes compliance, so author the final metadata
        # before the upload package is assembled rather than packaging stale fallbacks.
        _prepare_final_content_metadata(context)
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
        content_manifest = build_final_content_manifest(
            topic=context.request.topic,
            blueprint=context.payload,
            footage_evidence=context.footage_evidence,
            final_media_metadata=context.final_media_metadata,
            source_manifest=context.source_manifest,
        )
        atomic_write_json(context.package / "final_content_manifest.json", content_manifest)
        final_metadata = generate_final_metadata(
            content_manifest,
            source_manifest=context.source_manifest,
        )
        atomic_write_json(context.package / "final_metadata.json", final_metadata)
        summary = dict(context.baseline_summary)
        summary["content_manifest"] = content_manifest
        summary["final_metadata"] = final_metadata
        summary["source_manifest"] = context.source_manifest
        context.baseline_summary = summary
        context.metadata_report = build_upload_metadata(
            context.request.topic,
            summary=summary,
            hook=str(final_metadata.get("selected_title") or content_manifest.get("hook") or ""),
            attribution=str(source_meta.get("attribution") or ""),
        )
        # Final metadata is authoritative; guardrails validate exactly what will be published.
        context.metadata_report["title"] = str(final_metadata.get("selected_title") or context.metadata_report["title"])
        context.metadata_report["description"] = str(final_metadata.get("description") or context.metadata_report["description"])
        context.metadata_report["hashtags"] = list(final_metadata.get("hashtags") or context.metadata_report["hashtags"])
        context.metadata_report["thumbnail_concept"] = str(
            final_metadata.get("thumbnail_concept")
            or context.metadata_report.get("thumbnail_concept")
            or ""
        )
        validated = validate_metadata(
            context.metadata_report["title"],
            context.metadata_report["description"],
            context.metadata_report["hashtags"],
        )
        context.metadata_report["quality"] = {
            **dict(context.metadata_report.get("quality") or {}),
            "ok": bool(validated["ok"]),
            "errors": list(validated["errors"]),
            "score": validated["score"],
            "duplicate_phrase_score": validated["duplicate_phrase_score"],
        }
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
            upload_package_required=False,
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


class StageIdentityStage:
    name = "stage_identity"

    def run(self, context: V3ExecutionContext) -> None:
        atomic_write_json(
            context.package / "v3_stage_cache.json",
            {
                "version": "1.0.0",
                "stages": dict(context.stage_cache),
                "reuse_policy": "identity-only; stages are not blindly skipped until their output contracts are declared",
            },
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
                "editorial_decisions.json",
                "source_manifest.json",
                "scenes.json",
                "creative_provenance.json",
                "job_identity.json",
                "final_content_manifest.json",
                "final_metadata.json",
                "clip_source_evidence.json",
                "v3_evaluation_report.json",
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





def _last_good_snapshot_paths(package: Path) -> tuple[Path, Path, Path]:
    """Return package-specific durable snapshot, manifest, and in-progress-copy paths."""
    package_root = package.resolve()
    token = hashlib.sha256(os.fsencode(str(package_root))).hexdigest()[:20]
    snapshot = package_root.parent / f".aivf-v3-last-good-{token}.mp4"
    manifest = snapshot.with_name(snapshot.name + ".json")
    partial = snapshot.with_name(snapshot.name + ".partial")
    return snapshot, manifest, partial


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory fsync so adjacent atomic renames survive a hard reset."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(directory), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        # Windows does not expose directory fsync in the same way as POSIX.
        pass
    finally:
        os.close(descriptor)


def _remove_snapshot_artifacts(snapshot: Path) -> None:
    """Remove only this package's snapshot files; never follow symlinks."""
    for path in (
        snapshot,
        snapshot.with_name(snapshot.name + ".json"),
        snapshot.with_name(snapshot.name + ".partial"),
    ):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
        except OSError:
            # Retaining an orphan is safer than allowing a cleanup error to hide a good copy.
            continue
    _fsync_directory(snapshot.parent)


def _probe_and_decode_video(
    path: Path,
    *,
    target_seconds: float | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> bool:
    """Return true only for a regular, probeable video that fully decodes."""
    if path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size <= 0:
            return False
        media = probe_media(str(path))
        duration = float(media.get("duration") or 0.0)
        width = int(media.get("width") or 0)
        height = int(media.get("height") or 0)
        if not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
            return False
        if target_seconds is not None and (
            not math.isfinite(float(target_seconds))
            or abs(duration - float(target_seconds)) > 0.08
        ):
            return False
        if expected_width is not None and width != int(expected_width):
            return False
        if expected_height is not None and height != int(expected_height):
            return False
        from .v3_quality import _verify_full_decode
        _verify_full_decode(str(path))
        return True
    except Exception:
        return False


def _has_verified_completed_workspace(package: Path) -> bool:
    """Recognize a newer completed result before attempting to restore an older snapshot."""
    final_path = package / "final.v3.mp4"
    identity_path = package / "job_identity.json"
    readiness_path = package / "v3_readiness.json"
    manifest_path = package / "artifact_manifest.json"
    if not all(path.is_file() and not path.is_symlink() for path in (
        final_path, identity_path, readiness_path, manifest_path
    )):
        return False
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(identity, Mapping) or not isinstance(readiness, Mapping) or not isinstance(manifest, Mapping):
        return False
    if not all(str(identity.get(key) or "") for key in (
        "job_id", "source_hash", "request_hash", "configuration_hash", "blueprint_hash"
    )):
        return False
    checks = readiness.get("checks") or {}
    if (
        readiness.get("state") not in {"UPLOAD_PACKAGE_VALID", "PUBLISH_READY"}
        or not isinstance(checks, Mapping)
        or checks.get("UPLOAD_PACKAGE_VALID") is not True
        or bool(readiness.get("errors"))
    ):
        return False
    integrity = verify_artifact_manifest(package, manifest, verify_hashes=True)
    if not integrity.get("ok"):
        return False
    final_entries = [
        entry for entry in (manifest.get("files") or [])
        if isinstance(entry, Mapping) and entry.get("path") == "final.v3.mp4"
    ]
    if len(final_entries) != 1:
        return False
    recorded_hash = str(final_entries[0].get("sha256") or "")
    try:
        if not recorded_hash or sha256_file(final_path) != recorded_hash:
            return False
    except (OSError, ValueError):
        return False

    target_seconds = None
    expected_width = None
    expected_height = None
    fingerprint_path = package / "environment_fingerprint.json"
    try:
        fingerprint = json.loads(fingerprint_path.read_text(encoding="utf-8"))
        target = fingerprint.get("target_seconds")
        if target is not None:
            target_seconds = float(target)
        profile = fingerprint.get("platform_profile") or {}
        if isinstance(profile, Mapping):
            if profile.get("width") is not None:
                expected_width = int(profile["width"])
            if profile.get("height") is not None:
                expected_height = int(profile["height"])
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        # Older successful packages may not contain this optional fingerprint.
        pass
    return _probe_and_decode_video(
        final_path,
        target_seconds=target_seconds,
        expected_width=expected_width,
        expected_height=expected_height,
    )


def _snapshot_manifest_is_valid(
    package: Path,
    snapshot: Path,
    manifest_path: Path,
) -> tuple[bool, dict[str, Any] | None, str]:
    """Authenticate the durable snapshot against its fsynced sidecar before restore."""
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return False, None, "snapshot manifest is missing or is not a regular file"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False, None, "snapshot manifest is unreadable"
    if not isinstance(manifest, Mapping):
        return False, None, "snapshot manifest is not an object"
    if (
        manifest.get("schema_version") != 1
        or manifest.get("package_path") != str(package.resolve())
        or manifest.get("final_path") != str((package / "final.v3.mp4").resolve())
        or manifest.get("snapshot_filename") != snapshot.name
    ):
        return False, None, "snapshot manifest does not belong to this package"
    if snapshot.is_symlink() or not snapshot.is_file():
        return False, dict(manifest), "snapshot file is missing or is not a regular file"
    try:
        expected_size = int(manifest.get("snapshot_size_bytes") or 0)
        expected_hash = str(manifest.get("snapshot_sha256") or "").lower()
        original_size = int(manifest.get("original_size_bytes") or 0)
        original_hash = str(manifest.get("original_sha256") or "").lower()
        actual_size = snapshot.stat().st_size
        actual_hash = sha256_file(snapshot).lower()
    except (OSError, TypeError, ValueError):
        return False, dict(manifest), "snapshot could not be inspected"
    if (
        expected_size <= 0
        or expected_size != actual_size
        or original_size != expected_size
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
        or expected_hash != actual_hash
        or original_hash != expected_hash
    ):
        return False, dict(manifest), "snapshot size or SHA-256 does not match its durable manifest"
    return True, dict(manifest), ""


def _recover_result(package: Path, message: str, *, snapshot: Path | None = None) -> ProductionResult:
    """Build a failure result for interrupted recovery so callers cannot report success."""
    result = ProductionResult(package_dir=str(package))
    result.errors.append(message)
    if snapshot is not None and (snapshot.exists() or snapshot.is_symlink()):
        result.artifacts["last_good_snapshot"] = str(snapshot)
    final_path = package / "final.v3.mp4"
    if final_path.is_file() and not final_path.is_symlink():
        result.artifacts["last_good_final_preserved"] = str(final_path)
    try:
        _write_failed_evidence(package, result.errors)
        result.artifacts["v3_failure"] = str(package / "v3_failure.json")
        result.artifacts["v3_readiness"] = str(package / "v3_readiness.json")
    except Exception as exc:
        result.errors.append(
            f"CRITICAL: could not persist interrupted-render failure state: {type(exc).__name__}: {exc}"
        )
    return result


def _restore_recovered_snapshot(
    package: Path,
    snapshot: Path,
    *,
    manifest_path: Path | None = None,
    partial_path: Path | None = None,
) -> ProductionResult:
    """Atomically restore a validated old final and require an explicit retry."""
    final_path = package / "final.v3.mp4"
    if final_path.exists() and final_path.is_dir() and not final_path.is_symlink():
        return _recover_result(
            package,
            "Cannot restore the previous final because final.v3.mp4 is a directory; recovery files were retained.",
            snapshot=snapshot,
        )
    try:
        os.replace(snapshot, final_path)
        _fsync_directory(final_path.parent)
        _fsync_directory(snapshot.parent)
    except OSError as exc:
        return _recover_result(
            package,
            f"CRITICAL: could not restore the last-known-good video after an interrupted render: {type(exc).__name__}: {exc}",
            snapshot=snapshot,
        )
    # The video is now restored; leftover metadata must never make the failed attempt look successful.
    if manifest_path is not None:
        try:
            manifest_path.unlink(missing_ok=True)
        except OSError:
            pass
    if partial_path is not None:
        try:
            partial_path.unlink(missing_ok=True)
        except OSError:
            pass
    _fsync_directory(snapshot.parent)
    return _recover_result(
        package,
        "An interrupted V3 render was detected. The previous final.v3.mp4 was restored; retry the job to render again.",
    )


def _recover_abandoned_last_good_snapshot(package: Path) -> ProductionResult | None:
    """Recover durable/legacy last-good snapshots before rerunning or reusing the package.

    A fully verified completed package always wins over an older snapshot. This prevents
    a crash after publishing a new successful result but before deleting its backup from
    rolling the package back to an older video.
    """
    package = package.resolve()
    snapshot, manifest_path, partial_path = _last_good_snapshot_paths(package)
    final_path = package / "final.v3.mp4"
    stable_artifacts = (
        snapshot.exists() or snapshot.is_symlink()
        or manifest_path.exists() or manifest_path.is_symlink()
        or partial_path.exists() or partial_path.is_symlink()
    )
    legacy_snapshots = [
        path for path in sorted(snapshot.parent.glob(".aivf-v3-last-good-*.mp4"))
        if path != snapshot and (path.is_file() or path.is_symlink())
    ]
    if not stable_artifacts and not legacy_snapshots:
        return None

    if _has_verified_completed_workspace(package):
        _remove_snapshot_artifacts(snapshot)
        # Legacy snapshots have no owner manifest. Keep them rather than risk deleting a
        # different package's only backup when callers use a shared parent directory.
        return None

    if len(legacy_snapshots) > 1:
        return _recover_result(
            package,
            "Multiple orphaned legacy last-good snapshots were found. Automatic recovery refused to guess which file belongs to this package; all snapshots were retained.",
        )
    if legacy_snapshots:
        # Older snapshots did not carry an owner manifest. Only associate one when this
        # package is the sole child workspace in its parent (the dashboard's per-attempt layout).
        try:
            sibling_workspaces = [
                item for item in snapshot.parent.iterdir()
                if item.is_dir() and not item.is_symlink()
            ]
        except OSError:
            sibling_workspaces = []
        if len(sibling_workspaces) > 1 or (
            len(sibling_workspaces) == 1 and sibling_workspaces[0].resolve() != package
        ):
            return _recover_result(
                package,
                "An orphaned legacy snapshot has no owner manifest and this parent contains multiple workspaces; recovery refused to guess. The snapshot was retained.",
                snapshot=legacy_snapshots[0],
            )
        legacy = legacy_snapshots[0]
        if not _probe_and_decode_video(legacy):
            return _recover_result(
                package,
                f"An orphaned legacy snapshot could not be validated as a playable video; it was retained at {legacy}.",
                snapshot=legacy,
            )
        return _restore_recovered_snapshot(package, legacy)

    if not manifest_path.exists() and not manifest_path.is_symlink():
        # The durable implementation does not reset the package until the manifest is
        # written. If the copy/manifest was interrupted while the original is still in
        # place, compare bytes before discarding the incomplete transaction.
        if partial_path.exists() or partial_path.is_symlink():
            if snapshot.exists() or snapshot.is_symlink():
                if final_path.is_file() and not final_path.is_symlink():
                    try:
                        same_as_original = (
                            snapshot.is_file()
                            and not snapshot.is_symlink()
                            and sha256_file(final_path) == sha256_file(snapshot)
                        )
                    except OSError:
                        same_as_original = False
                    if same_as_original:
                        _remove_snapshot_artifacts(snapshot)
                        return None
                return _recover_result(
                    package,
                    "An incomplete last-good snapshot has no durable manifest and cannot be restored safely; the recovery file was retained.",
                    snapshot=snapshot,
                )
            if final_path.is_file() and not final_path.is_symlink():
                _remove_snapshot_artifacts(snapshot)
                return None
            return _recover_result(
                package,
                "An interrupted snapshot copy was found but the original final is missing and no durable manifest exists; recovery files were retained.",
                snapshot=snapshot,
            )
        if snapshot.exists() or snapshot.is_symlink():
            if final_path.is_file() and not final_path.is_symlink():
                try:
                    same_as_original = (
                        snapshot.is_file()
                        and not snapshot.is_symlink()
                        and sha256_file(final_path) == sha256_file(snapshot)
                    )
                except OSError:
                    same_as_original = False
                if same_as_original:
                    _remove_snapshot_artifacts(snapshot)
                    return None
            return _recover_result(
                package,
                "A last-good snapshot exists without its durable manifest. Automatic restore refused to trust an untracked copy; the snapshot was retained.",
                snapshot=snapshot,
            )
        return None

    valid, manifest, reason = _snapshot_manifest_is_valid(package, snapshot, manifest_path)
    if not valid or manifest is None:
        return _recover_result(
            package,
            f"Last-good snapshot verification failed: {reason}. Recovery files were retained and the rerun was blocked.",
            snapshot=snapshot,
        )

    # A crash before workspace reset leaves the old canonical bytes intact. In that case
    # the snapshot is redundant and may be discarded; the next run will make a fresh copy.
    if final_path.is_file() and not final_path.is_symlink():
        try:
            current_hash = sha256_file(final_path).lower()
        except OSError:
            current_hash = ""
        if current_hash and current_hash == str(manifest["original_sha256"]).lower():
            _remove_snapshot_artifacts(snapshot)
            return None

    return _restore_recovered_snapshot(
        package,
        snapshot,
        manifest_path=manifest_path,
        partial_path=partial_path,
    )


def _snapshot_last_good_final(package: Path) -> Path | None:
    """Durably snapshot the current final before destructive rerun work begins."""
    final_path = package / "final.v3.mp4"
    if not final_path.exists() and not final_path.is_symlink():
        return None
    if final_path.is_symlink() or not final_path.is_file():
        raise V3InputError("existing final.v3.mp4 is not a regular file; refusing to overwrite it")
    original_size = final_path.stat().st_size
    if original_size <= 0:
        raise V3InputError("existing final.v3.mp4 is empty; refusing to overwrite it automatically")
    reserve_bytes = 128 * 1024 * 1024
    try:
        free_bytes = shutil.disk_usage(package.parent).free
    except OSError as exc:
        raise V3InputError("cannot inspect free disk space before protecting the existing final video") from exc
    if free_bytes < original_size + reserve_bytes:
        raise V3InputError(
            f"rerun blocked to preserve the existing final video: need at least "
            f"{original_size + reserve_bytes} bytes free for a safe snapshot; have {free_bytes}"
        )

    snapshot, manifest_path, partial_path = _last_good_snapshot_paths(package)
    if any(path.exists() or path.is_symlink() for path in (snapshot, manifest_path, partial_path)):
        raise V3InputError(
            "an abandoned last-good snapshot exists and must be recovered before another rerun"
        )
    original_hash = sha256_file(final_path)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    descriptor = os.open(str(partial_path), flags, 0o600)
    try:
        with final_path.open("rb") as source, os.fdopen(descriptor, "wb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        if (
            partial_path.stat().st_size != original_size
            or sha256_file(partial_path) != original_hash
        ):
            raise V3InputError("last-good final changed during snapshot; refusing to start rerun")
        os.replace(partial_path, snapshot)
        _fsync_directory(snapshot.parent)
        manifest = {
            "schema_version": 1,
            "package_path": str(package.resolve()),
            "final_path": str(final_path.resolve()),
            "snapshot_filename": snapshot.name,
            "snapshot_size_bytes": original_size,
            "snapshot_sha256": original_hash,
            "original_size_bytes": original_size,
            "original_sha256": original_hash,
            "created_at_unix": time.time(),
        }
        atomic_write_json(manifest_path, manifest)
        _fsync_directory(snapshot.parent)
        return snapshot
    except BaseException:
        # Before this function returns, the destructive workspace reset has not started.
        # Clean only if the original canonical bytes are still present and unchanged.
        try:
            unchanged = (
                final_path.is_file()
                and not final_path.is_symlink()
                and sha256_file(final_path) == original_hash
            )
        except OSError:
            unchanged = False
        if unchanged:
            _remove_snapshot_artifacts(snapshot)
        raise


def _finish_rerun_with_last_good_protection(
    result: ProductionResult,
    package: Path,
    backup_path: Path | None,
) -> ProductionResult:
    """Keep a previous final if the rerun fails, and reject false-success results."""
    final_path = package / "final.v3.mp4"
    reported_final = Path(result.final_video).resolve() if result.final_video else None
    success = (
        not result.errors
        and reported_final == final_path.resolve()
        and final_path.is_file()
        and not final_path.is_symlink()
        and final_path.stat().st_size > 0
    )
    if success:
        try:
            probe_media(str(final_path))
        except Exception as exc:
            result.errors.append(
                f"final artifact failed post-run media validation: {type(exc).__name__}: {exc}"
            )
            success = False
    if success:
        if backup_path is not None:
            _remove_snapshot_artifacts(backup_path)
        return result

    try:
        if backup_path is not None:
            os.replace(backup_path, final_path)
            _fsync_directory(final_path.parent)
            _fsync_directory(backup_path.parent)
            _remove_snapshot_artifacts(backup_path)
            result.artifacts["last_good_final_preserved"] = str(final_path)
            result.warnings.append(
                "The rerun failed; the previous final.v3.mp4 was restored and preserved."
            )
        else:
            if final_path.is_file() or final_path.is_symlink():
                final_path.unlink(missing_ok=True)
            result.artifacts["failed_final_removed"] = str(final_path)
    except OSError as exc:
        result.errors.append(
            f"CRITICAL: failed to restore the previous final video safely: {type(exc).__name__}: {exc}"
        )
        result.artifacts["last_good_snapshot"] = str(backup_path) if backup_path else ""
        return result
    result.final_video = None
    if not result.errors:
        result.errors.append("render did not produce a validated canonical final video")
    if backup_path is None:
        _write_failed_evidence(package, result.errors)
    return result


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
            SourceAnalysisStage(),
            PlanningStage(),
            RenderStage(),
            MediaValidationStage(),
            PackagingStage(),
            ComplianceStage(),
            EvaluationCaptureStage(),
            StageIdentityStage(),
            ReleaseEvidenceStage(),
        ))

    def run(self) -> ProductionResult:
        backup_path: Path | None = None
        package = Path(self.request.package_dir)
        try:
            with WorkspaceLock(self.request.package_dir):
                recovered = _recover_abandoned_last_good_snapshot(package)
                if recovered is not None:
                    return recovered
                reused = _reuse_existing_job(
                    self.request,
                    source_metadata=self.source_metadata,
                )
                if reused is not None:
                    return reused
                try:
                    backup_path = _snapshot_last_good_final(package)
                except (OSError, V3PipelineError) as exc:
                    # The last final remains untouched. Don't invalidate its existing
                    # manifests merely because the new run could not safely start.
                    blocked = ProductionResult(package_dir=str(package))
                    blocked.errors.append(f"rerun preflight blocked before workspace reset: {exc}")
                    if (package / "final.v3.mp4").is_file() and not (package / "final.v3.mp4").is_symlink():
                        blocked.artifacts["last_good_final_preserved"] = str(package / "final.v3.mp4")
                    return blocked
                try:
                    result = self._run_locked()
                    return _finish_rerun_with_last_good_protection(result, package, backup_path)
                except BaseException:
                    if backup_path is not None and backup_path.exists():
                        os.replace(backup_path, package / "final.v3.mp4")
                        _fsync_directory((package / "final.v3.mp4").parent)
                        _remove_snapshot_artifacts(backup_path)
                    else:
                        failed_final = package / "final.v3.mp4"
                        if failed_final.is_file() or failed_final.is_symlink():
                            failed_final.unlink(missing_ok=True)
                    raise
                # Snapshot helpers consume their file on success/restore. If restoration
                # itself fails, retain the orphaned snapshot rather than deleting the only
                # recoverable copy of the previous final.
        except WorkspaceBusyError as exc:
            # The workspace belongs to another active job. Never mutate its artifacts
            # or readiness state from the losing caller.
            result = ProductionResult(package_dir=str(self.request.package_dir))
            result.errors.append(str(exc))
            return result

    def _run_locked(self) -> ProductionResult:
        context = V3ExecutionContext(
            request=self.request,
            source_metadata=self.source_metadata,
            package=Path(self.request.package_dir),
        )
        try:
            _guard_workspace_isolation(self.request, source_metadata=self.source_metadata)
            _reset_v3_package(
                context.package,
                input_video=self.request.input_video,
            )
        except V3PipelineError as exc:
            context.result.errors.append(f"[preflight] {exc}")
            _write_failed_evidence(
                context.package,
                context.result.errors,
                context.stage_timings_ms,
            )
            context.result.artifacts.update({
                "v3_failure": str(context.package / "v3_failure.json"),
                "v3_readiness": str(context.package / "v3_readiness.json"),
            })
            return context.result

        try:
            source_hash = file_hash(self.request.input_video)
        except (OSError, ValueError):
            source_hash = "unavailable"
        cache_records: dict[str, dict[str, Any]] = {}
        for stage in self.stages:
            started = time.perf_counter()
            stage_version = "1.0.0"
            key = stage_cache_key(source_hash=source_hash, stage_name=stage.name, stage_version=stage_version, configuration={"topic": self.request.topic, "context": self.request.context, "target_seconds": self.request.target_seconds, "platform": self.request.platform, "audience": self.request.audience, "bpm": self.request.bpm, "edit_type": self.request.edit_type, "model_key": self.request.model_key, "enable_ocr": self.request.enable_ocr, "enable_object_detection": self.request.enable_object_detection, "enable_diarization": self.request.enable_diarization})
            try:
                stage.run(context)
            except V3PipelineError as exc:
                context.result.errors.append(f"[{stage.name}] {exc}")
                break
            finally:
                context.stage_timings_ms[stage.name] = round((time.perf_counter() - started) * 1000.0, 3)
                context.stage_cache[stage.name] = cache_record(key=key, stage_name=stage.name, stage_version=stage_version, inputs=[self.request.input_video], outputs=[])

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
                    "resource_limits": {
                        "container_cpus": os.environ.get("AIVF_CONTAINER_CPUS", "not-configured"),
                        "container_memory": os.environ.get("AIVF_CONTAINER_MEMORY", "not-configured"),
                        "container_pids": os.environ.get("AIVF_CONTAINER_PIDS", "not-configured"),
                        "max_concurrent_jobs": os.environ.get("AIVF_MAX_CONCURRENT_JOBS", "1"),
                        "max_job_memory_mb": os.environ.get("AIVF_MAX_JOB_MEMORY_MB", "not-configured"),
                        "max_reserved_memory_mb": os.environ.get("AIVF_MAX_RESERVED_MEMORY_MB", "not-configured"),
                    },
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
                # A failed rerun must never leave a previous READY readiness artifact.
                _write_failed_evidence(
                    context.package,
                    context.result.errors,
                    context.stage_timings_ms,
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
            "v3_stage_cache",
            "editorial_decisions",
            "source_manifest",
            "scenes",
            "creative_provenance",
            "job_identity",
            "final_content_manifest",
            "final_metadata",
            "clip_source_evidence",
            "v3_evaluation_report",
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
        _cleanup_transients(context.package, preserve=bool(context.result.errors) or os.environ.get("AIVF_DEBUG_ARTIFACTS", "0") == "1")
        return context.result


__all__ = [
    "ComplianceStage",
    "InputValidationStage",
    "MediaValidationStage",
    "PackagingStage",
    "EvaluationCaptureStage",
    "PlanningStage",
    "ReleaseEvidenceStage",
    "RenderStage",
    "SourceAnalysisStage",
    "V3ExecutionContext",
    "V3PipelineRunner",
    "V3Stage",
]
