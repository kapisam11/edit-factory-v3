from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_video_factory.v3.audience import parse_audience
from ai_video_factory.v3.effects import EffectCompiler, EffectKind
from ai_video_factory.v3.hook_eval import evaluate_hook_candidates, generate_hook_candidates
from ai_video_factory.v3.job_identity import job_identity
from ai_video_factory.v3.migrations import migrate_to_current
from ai_video_factory.v3.schema import validate_blueprint_payload
from ai_video_factory.v3.source_manifest import build_source_manifest
from ai_video_factory.v3.visual_qc import verify_visual_effect
from ai_video_factory.v3.workspace import WorkspaceBusyError, WorkspaceLock
from ai_video_factory.v3_engine import EditType, V3Config, V3Blueprint, create_v3_blueprint


def test_blueprint_rejects_unknown_top_level_field():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    payload = blueprint.to_dict()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        validate_blueprint_payload(payload)


def test_blueprint_rejects_missing_required_field():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    payload = blueprint.to_dict()
    payload.pop("clip_plan")
    with pytest.raises(ValueError, match="missing required fields"):
        validate_blueprint_payload(payload)


def test_legacy_source_metadata_is_migrated_out():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    payload = blueprint.to_dict()
    payload["source_metadata"] = {"rights_status": "owned"}
    migrated = migrate_to_current(payload)
    assert "source_metadata" not in migrated
    assert V3Blueprint.from_dict(migrated).version == "3.0.0"


def test_technical_validity_is_unknown_before_render():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    assert blueprint.score_bundle.technical_validity is None


def test_unsupported_planning_language_is_rejected():
    with pytest.raises(ValueError, match="unsupported planning language"):
        V3Config(target_seconds=8, language="de").validate()


def test_numeric_domain_objects_do_not_silently_coerce():
    with pytest.raises(TypeError):
        V3Config(target_seconds="8").validate()
    with pytest.raises(TypeError):
        V3Config(bpm=120.0).validate()


def test_platform_policy_is_versioned_and_typed():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8, platform="youtube_shorts"))
    assert blueprint.platform_constraints.policy_version
    assert blueprint.platform_constraints.width == 1080
    assert blueprint.platform_constraints.height == 1920


def test_extra_capability_is_allowed_while_required_capabilities_remain_enforced():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    payload = blueprint.to_dict()
    payload["capabilities"] = list(payload["capabilities"]) + ["future-capability"]
    restored = V3Blueprint.from_dict(payload)
    assert "future-capability" in restored.capabilities


def test_hook_candidates_are_evaluated_from_candidate_features():
    candidates = generate_hook_candidates(
        "Minecraft clutch",
        "the result was uncertain",
        "one decision mattered",
        "the final proof resolves the question",
        "Dramatic",
    )
    evaluated = evaluate_hook_candidates(
        candidates,
        "Minecraft clutch",
        "the result was uncertain",
        "one decision mattered",
        "the final proof resolves the question",
    )
    scores = [item[1].score for item in evaluated]
    assert len(set(scores)) > 1
    assert all(item[1].evaluator for item in evaluated)
    assert all(item[1].reasons for item in evaluated)


def test_effect_compiler_makes_low_confidence_a_successful_noop():
    effect = EffectCompiler().compile_retention({
        "time": 2.0,
        "kind": "zoom",
        "confidence": 0.2,
        "reason": "LOW_VALUE",
    })
    assert effect.kind is EffectKind.DO_NOTHING
    assert effect.duration == 0.0


def test_visual_qc_rejects_unsupported_effect_kind():
    result = verify_visual_effect(b"a" * 100, b"b" * 100, effect_kind="unknown")
    assert result.passed is False


def test_visual_qc_distinguishes_pixel_change_and_semantic_kind():
    result = verify_visual_effect(
        bytes(range(100)),
        bytes(reversed(range(100))),
        effect_kind="zoom",
        expected_change=0.1,
    )
    assert result.pixel_delta >= 0.1
    assert result.reason


def test_source_manifest_is_content_addressed(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-data")
    manifest = build_source_manifest(source, {"rights_status": "owned"})
    assert len(manifest.source_sha256) == 64
    assert manifest.rights_status == "owned"


def test_workspace_lock_rejects_concurrent_owner(tmp_path):
    workspace = tmp_path / "job"
    first = WorkspaceLock(workspace)
    second = WorkspaceLock(workspace)
    first.acquire()
    try:
        with pytest.raises(WorkspaceBusyError):
            second.acquire()
    finally:
        first.release()


def test_job_identity_changes_when_a_contract_component_changes():
    common = {
        "source_hash": "a" * 64,
        "blueprint_hash": "b" * 64,
        "config_hash": "c" * 64,
        "renderer_version": "3.0.0",
        "platform_policy_version": "2026-10-contract-1",
    }
    base = job_identity(**common)
    changed = job_identity(**{**common, "renderer_version": "3.0.1"})
    assert base != changed


def test_editorial_decision_graph_can_choose_do_nothing_without_a_render_effect():
    from ai_video_factory.editorial_evaluation import build_retention_decisions
    decisions = build_retention_decisions([
        {"time": 1.5, "kind": "motion", "confidence": 0.10, "reason": "NO_SUPPORTED_EVENT"},
    ])
    assert decisions[0].operation == "DO_NOTHING"


def test_heuristic_metric_names_are_explicit():
    from ai_video_factory.v3_scoring import heuristic_metrics
    metrics = heuristic_metrics(hook=0.9, pace=0.8, quality=0.9, emotion=0.8)
    assert metrics["retention_heuristic"] == metrics["retention_score"]
    assert "completion_heuristic" in metrics
    assert "rewatch_heuristic" in metrics
    assert "shareability_heuristic" in metrics


def test_round_trip_preserves_clip_and_hook_evidence():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    restored = V3Blueprint.from_dict(json.loads(json.dumps(blueprint.to_dict())))
    assert restored.hooks == blueprint.hooks
    assert restored.clip_plan == blueprint.clip_plan
