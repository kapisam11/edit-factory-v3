"""Stage-oriented V3 execution without changing the public pipeline API."""
from __future__ import annotations

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
from .v3_quality import RenderContractError, enforce_retention_events, normalize_duration, strict_render_check
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


class EvaluationCaptureStage:
    """Capture the produced job in the empirical corpus without fabricating labels."""

    name = "evaluation_capture"

    def run(self, context: V3ExecutionContext) -> None:
        if context.result.errors or not context.result.final_video:
            return
        from .editorial_corpus import CorpusCase, EditorialCorpus

        corpus_path = Path(
            os.environ.get(
                "AIVF_EDITORIAL_CORPUS_PATH",
                str(context.package / "editorial_corpus.sqlite"),
            )
        )
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
            },
        )
        corpus = EditorialCorpus(corpus_path)
        corpus.add_case(case)
        corpus.add_blueprint_predictions(
            context.job_id,
            context.payload.get("metrics", {}),
            confidence=float(
                context.blueprint.score_bundle.performance_confidence
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
        try:
            with WorkspaceLock(self.request.package_dir):
                reused = _reuse_existing_job(
                    self.request,
                    source_metadata=self.source_metadata,
                )
                if reused is not None:
                    return reused
                return self._run_locked()
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
