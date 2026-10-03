"""Stage-level V3 blueprint validation implementation.

Kept outside v3_engine so validation rules are independently testable while
v3_engine remains a backward-compatible public facade.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from pathlib import Path
import re

from typing import Any

from .. import v3_engine as engine


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text).lower())


def validate_blueprint(blueprint: "engine.V3Blueprint") -> None:
    if not isinstance(blueprint, engine.V3Blueprint):
        raise ValueError("expected a engine.V3Blueprint instance")
    if blueprint.version != "3.0.0":
        raise ValueError("unexpected blueprint version")
    from ..v3_capabilities import REQUIRED_CAPABILITIES
    available_capabilities = set(blueprint.capabilities)
    missing_capabilities = sorted(REQUIRED_CAPABILITIES - available_capabilities)
    if missing_capabilities:
        raise ValueError("v3 blueprint is missing required capabilities: " + ", ".join(missing_capabilities))
    if len(available_capabilities) != len(blueprint.capabilities):
        raise ValueError("v3 blueprint capabilities must be unique")
    if blueprint.platform not in engine.PLATFORM_PROFILES:
        raise ValueError(f"unsupported blueprint platform: {blueprint.platform}")
    expected_profile = engine.PLATFORM_PROFILES[blueprint.platform]
    if asdict(blueprint.platform_constraints) != dict(expected_profile):
        raise ValueError("blueprint platform profile does not match registered platform constraints")
    if not 0.01 <= float(blueprint.qc.target_tolerance_seconds) <= 1.0:
        raise ValueError("blueprint QC target tolerance is invalid")
    if blueprint.metric_metadata.method != "heuristic" or blueprint.metric_metadata.confidence not in {"low", "medium", "high"}:
        raise ValueError("blueprint metric metadata is invalid")
    if blueprint.metric_metadata.calibration_status not in {"uncalibrated", "calibrated"}:
        raise ValueError("blueprint metric calibration status is invalid")
    for score_name, score_value in (
        ("technical_validity", blueprint.score_bundle.technical_validity),
        ("creative_quality", blueprint.score_bundle.creative_quality),
        ("performance_heuristic", blueprint.score_bundle.performance_heuristic),
    ):
        if score_value is None:
            if score_name != "technical_validity":
                raise ValueError(f"blueprint {score_name} cannot be null")
            continue
        if not math.isfinite(float(score_value)) or not 0.0 <= float(score_value) <= 100.0:
            raise ValueError(f"blueprint {score_name} is invalid")

    if not str(blueprint.audience).strip():
        raise ValueError("blueprint audience is required")
    if not blueprint.platform_variants or blueprint.platform not in blueprint.platform_variants:
        raise ValueError("blueprint platform variants are incomplete")
    try:
        edit_type = engine.EditType(blueprint.edit_type)
    except ValueError as exc:
        raise ValueError(f"unsupported blueprint edit type: {blueprint.edit_type}") from exc
    if edit_type not in engine.EDIT_STRATEGIES or not blueprint.clip_plan:
        raise ValueError("blueprint is missing a registered creative strategy")
    if not blueprint.retention_map:
        raise ValueError("blueprint retention map is empty")
    previous_retention = -1e-6
    for event in blueprint.retention_map:
        try:
            engine.RetentionKind(event.kind)
        except ValueError as exc:
            raise ValueError(f"unsupported retention event kind: {event.kind}") from exc
        if not math.isfinite(float(event.time)) or event.time < -0.001 or event.time > blueprint.duration + 0.001:
            raise ValueError("retention event time is outside the blueprint timeline")
        if event.time < previous_retention:
            raise ValueError("retention map is not monotonic")
        if not str(event.instruction).strip():
            raise ValueError("retention event instruction is required")
        previous_retention = event.time
    previous = -1e-6
    for expected_index, clip in enumerate(blueprint.clip_plan, start=1):
        if clip.index != expected_index:
            raise ValueError("clip plan indices must be contiguous and 1-based")
        if not all(math.isfinite(float(value)) for value in (clip.start, clip.end)):
            raise ValueError(f"clip {clip.index} contains non-finite timing")
        if clip.start < previous or clip.end <= clip.start:
            raise ValueError("clip plan is not monotonic")
        if not 2 <= len(_tokens(clip.text_overlay)) <= 8:
            raise ValueError(f"invalid overlay word count at clip {clip.index}")
        previous = clip.end
    if not blueprint.music.sync_points or any(not math.isfinite(float(t)) for t in blueprint.music.sync_points):
        raise ValueError("blueprint music sync points are invalid")
    previous_sync = -1e-6
    for sync_point in blueprint.music.sync_points:
        if sync_point < previous_sync:
            raise ValueError("blueprint music sync points are not monotonic")
        if sync_point < -0.001 or sync_point > blueprint.duration + 0.001:
            raise ValueError("blueprint music sync point is outside the timeline")
        previous_sync = sync_point
    if abs(blueprint.clip_plan[-1].end - blueprint.music.sync_points[-1]) > 0.01:
        raise ValueError("clip plan does not exactly cover planned duration")

    titles = [str(title).strip() for title in blueprint.title_options if str(title).strip()]
    if len(titles) < 5:
        raise ValueError("blueprint title laboratory returned fewer than five usable titles")
    if len(set(title.lower() for title in titles)) != len(titles):
        raise ValueError("blueprint title options contain duplicates")
    if any(len(title) > 100 for title in titles):
        raise ValueError("blueprint title option exceeds the standard title limit")
    topic_tokens = set(_tokens(blueprint.core_idea.topic))
    top_title_tokens = set(_tokens(titles[0]))
    if topic_tokens and not topic_tokens.intersection(top_title_tokens):
        raise ValueError("blueprint top title is not specific to the requested topic")
    if len(str(blueprint.description).strip()) < 40:
        raise ValueError("blueprint description is too short")
    if not blueprint.hashtags:
        raise ValueError("blueprint hashtags are empty")
    if not any(set(_tokens(tag)).intersection(topic_tokens) for tag in blueprint.hashtags):
        raise ValueError("blueprint hashtags are not topic-specific")

    if not 0 <= int(blueprint.quality.score) <= 100:
        raise ValueError("blueprint quality score must be between 0 and 100")
    for key, value in blueprint.metrics.items():
        if not str(key).strip() or not math.isfinite(float(value)) or not 0 <= float(value) <= 100:
            raise ValueError(f"invalid heuristic metric: {key}")
    for hook in blueprint.hooks:
        if not all(isinstance(value, str) for value in (hook.visual, hook.text, hook.emotional)):
            raise ValueError("hook fields must be strings")
        if not math.isfinite(float(hook.score)) or not 0.0 <= float(hook.score) <= 1.0:
            raise ValueError("hook score must be between 0 and 1")
    for clip in blueprint.clip_plan:
        if not all(str(value).strip() for value in (clip.purpose, clip.emotion, clip.visual_style, clip.text_overlay, clip.camera_motion, clip.transition)):
            raise ValueError(f"clip {clip.index} contains empty creative fields")
    if not 40 <= int(blueprint.music.bpm) <= 240 or not math.isfinite(float(blueprint.music.beat_seconds)) or float(blueprint.music.beat_seconds) <= 0:
        raise ValueError("blueprint music settings are invalid")
    if not all(isinstance(tag, str) and tag.strip() for tag in blueprint.hashtags):
        raise ValueError("blueprint hashtags must be non-empty strings")
    if not all(isinstance(title, str) and title.strip() for title in blueprint.title_options):
        raise ValueError("blueprint title options must be non-empty strings")
    package_names = (
        blueprint.packaging.final_video_name,
        blueprint.packaging.thumbnail_name,
        blueprint.packaging.vertical_thumbnail_name,
        blueprint.packaging.upload_manifest_name,
    )
    if any(not isinstance(name, str) or not name.strip() or Path(name).name != name for name in package_names):
        raise ValueError("packaging artifact names must be simple filenames")
    if not isinstance(blueprint.qc.require_video, bool) or not isinstance(blueprint.qc.require_audio, bool) or not isinstance(blueprint.qc.require_independent_retention, bool):
        raise ValueError("blueprint QC flags must be booleans")
    for hook in blueprint.hooks:
        if hook.score_semantics not in {"editorial_heuristic_score", "legacy_template_priority"}:
            raise ValueError("hook score semantics are invalid")
        if not hook.evaluation or not hook.evaluation.get("evaluator"):
            raise ValueError("hook evaluation evidence is missing")
        source_evidence = float(hook.evaluation.get("source_evidence", 0.0))
        evaluated_score = float(hook.evaluation.get("score", hook.score))
        if not math.isfinite(source_evidence) or not 0.0 <= source_evidence <= 1.0:
            raise ValueError("hook source-evidence score is invalid")
        if abs(evaluated_score - float(hook.score)) > 0.001:
            raise ValueError("hook score does not match its evaluation")
    for clip in blueprint.clip_plan:
        if clip.evidence.evidence_status not in {"planned", "supported", "weak", "unsupported"}:
            raise ValueError(f"clip {clip.index} has invalid evidence status")
        if clip.evidence.evidence_status in {"supported", "weak"}:
            if not clip.evidence.source_asset or clip.evidence.source_end <= clip.evidence.source_start:
                raise ValueError(f"clip {clip.index} source evidence boundaries are invalid")
        if clip.evidence.evidence_status != "supported" and clip.purpose in {"Hook", "Payoff", "Punchline", "Climax", "Final impact"}:
            raise ValueError(f"critical clip {clip.index} has no supporting source evidence")

    if not blueprint.quality.passed:
        failed_checks = [
            name
            for name, passed in blueprint.quality.checks.items()
            if not passed
        ]
        detail = ", ".join(failed_checks) or "quality threshold"
        warnings = "; ".join(str(item) for item in blueprint.quality.warnings)
        suffix = f"; warnings={warnings}" if warnings else ""
        raise ValueError(
            f"blueprint failed strict editorial QC (score={blueprint.quality.score}; "
            f"failed={detail}{suffix})"
        )



__all__ = ["validate_blueprint"]
