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


def test_human_feedback_actions_are_structured_and_stored(tmp_path):
    from ai_video_factory.editorial_evaluation import HumanDecisionFeedback
    from ai_video_factory.feedback_store import FeedbackStore

    store = FeedbackStore(tmp_path / "feedback.sqlite")
    feedback = HumanDecisionFeedback(
        decision_id="decision-1",
        action="REJECT",
        reason_code="VISUALLY_UNMOTIVATED",
        severity=2,
        actor="editor",
        timestamp="2026-10-03T09:00:00Z",
        pipeline_version="3.0.0",
    )
    store.record_human_feedback(feedback)
    summary = store.editorial_summary()
    assert summary["human_feedback"]["samples"] == 1
    assert summary["human_feedback"]["actions"]["REJECT"] == 1


def test_legacy_schema_migrates_to_current_version():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    payload = blueprint.to_dict()
    payload["schema_version"] = "3.0.0"
    migrated = migrate_to_current(payload)
    assert migrated["schema_version"] == "3.0.1"
    assert migrated["migration_history"][-1]["from"] == "3.0.0"


def test_strict_schema_model_rejects_wrong_nested_types():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    payload = blueprint.to_dict()
    payload["quality"]["passed"] = "yes"
    with pytest.raises(ValueError):
        V3Blueprint.from_dict(payload)


def test_effect_compiler_owns_ffmpeg_graph_compilation():
    compiler = EffectCompiler()
    ir = compiler.compile([{"time": 1.0, "kind": "zoom", "confidence": 1.0}])
    graph = compiler.compile_ffmpeg_graph(ir)
    assert graph.video_filters
    assert graph.to_dict()["video_filters"] == list(graph.video_filters)


def test_structural_clip_event_compiles_to_noop():
    effect = EffectCompiler().compile_retention(
        {"time": 1.0, "kind": "clip", "confidence": 1.0}
    )
    assert effect.kind is EffectKind.DO_NOTHING
    assert effect.reason == "STRUCTURAL_CUT_HANDLED_BY_TIMELINE"


def test_zoom_qc_requires_spatial_evidence_not_only_global_pixel_delta():
    baseline = bytes([100]) * (160 * 90)
    rendered = bytes([120]) * (160 * 90)
    result = verify_visual_effect(
        baseline,
        rendered,
        effect_kind="zoom",
        expected_change=1.0,
    )
    assert result.passed is False
    assert result.semantic_signals["spatial_scale_change"] is False


def test_final_metadata_is_generated_from_manifest():
    from ai_video_factory.v3.metadata import generate_final_metadata
    result = generate_final_metadata(
        {
            "topic": "Minecraft clutch",
            "hook": "The final escape",
            "overlays": ["One detail mattered"],
            "clip_purposes": ["Hook", "Payoff"],
            "source_scenes": [{"description": "player escapes lava", "importance_score": 0.9}],
            "search_terms": ["minecraft"],
            "source_sha256": "a" * 64,
        }
    )
    assert result["source"] == "final_content_manifest"
    assert result["selected_title"]
    assert "minecraft" in " ".join(result["hashtags"]).lower()


def test_empirical_corpus_waits_for_real_samples(tmp_path):
    from ai_video_factory.editorial_corpus import CorpusCase, EditorialCorpus
    corpus = EditorialCorpus(tmp_path / "corpus.sqlite")
    corpus.add_case(CorpusCase("case-1", "video-1", "3.0.0", "3.0.0", {}))
    report = corpus.correlation_report()
    assert report["samples"] == 0
    assert all(
        item["status"] == "insufficient_samples"
        for item in report["dimensions"].values()
    )


def test_workspace_guard_rejects_foreign_generated_directory(tmp_path):
    from ai_video_factory import v3_stage_pipeline
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    (tmp_path / "v3_blueprint.json").write_text("{}", encoding="utf-8")
    request = v3_stage_pipeline.V3Request(
        input_video=str(source),
        topic="different",
        package_dir=str(tmp_path),
        target_seconds=8.0,
        platform="youtube_shorts",
        audience="general short-form viewers",
        bpm=120,
    )
    with pytest.raises(v3_stage_pipeline.V3InputError, match="dedicated jobs"):
        v3_stage_pipeline._guard_workspace_isolation(request)

def test_pydantic_schema_boundary_rejects_nested_unknown_field():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    payload = json.loads(json.dumps(blueprint.to_dict()))
    payload["core_idea"]["unexpected"] = True
    from ai_video_factory.v3.schema_models import validate_blueprint_model
    with pytest.raises(ValueError):
        validate_blueprint_model(payload)


def test_explicit_forward_schema_migration_is_registered():
    blueprint = create_v3_blueprint("A subject", config=V3Config(target_seconds=8))
    from ai_video_factory.v3.migrations import migrate_to_version
    migrated = migrate_to_version(blueprint.to_dict(), "3.1.0")
    assert migrated["schema_version"] == "3.1.0"
    assert migrated["migration_history"][-1]["from"] == "3.0.0"
    assert migrated["migration_history"][-1]["to"] == "3.1.0"


def test_request_config_does_not_coerce_numeric_strings():
    from ai_video_factory.v3_contracts import V3Request
    request = V3Request(
        input_video="input.mp4",
        topic="topic",
        package_dir="package",
        target_seconds="8",  # type: ignore[arg-type]
    )
    with pytest.raises(TypeError):
        request.config().validate()


def test_audience_pacing_changes_clip_density():
    fast = create_v3_blueprint(
        "A subject",
        config=V3Config(target_seconds=30, audience="fast gaming viewers"),
    )
    measured = create_v3_blueprint(
        "A subject",
        config=V3Config(target_seconds=30, audience="documentary history viewers"),
    )
    assert len(fast.clip_plan) >= len(measured.clip_plan)


def test_render_ir_compiler_handles_clip_as_structural_noop():
    from ai_video_factory.v3.effects import EffectCompiler, EffectKind, RenderIR
    ir = EffectCompiler().compile([{
        "time": 1.0,
        "kind": "clip",
        "confidence": 1.0,
        "reason": "STRUCTURAL_CUT",
    }])
    assert ir.effects[0].kind is EffectKind.DO_NOTHING
    graph = EffectCompiler().compile_ffmpeg_graph(RenderIR(effects=ir.effects))
    assert graph.video_filters == ()


def test_visual_qc_requires_effect_specific_signal():
    from ai_video_factory.v3.visual_qc import verify_visual_effect
    baseline = bytes([80] * (160 * 90))
    rendered = bytes([120] * (160 * 90))
    result = verify_visual_effect(
        baseline,
        rendered,
        effect_kind="caption",
        expected_change=1.0,
    )
    assert result.passed is False
    assert result.semantic_signal == result.lower_band_change


def test_corrupt_media_is_rejected_by_render_contract():
    from ai_video_factory.v3_quality import RenderContractError, probe_media
    broken = Path("broken.mp4")
    broken.write_bytes(b"not-a-real-mp4")
    with pytest.raises(RenderContractError):
        probe_media(str(broken))


def test_empirical_corpus_never_fabricates_labels(tmp_path):
    from ai_video_factory.editorial_corpus import CorpusCase, EditorialCorpus
    corpus = EditorialCorpus(tmp_path / "corpus.sqlite")
    corpus.add_case(CorpusCase("case-1", "video-1", "3.0.0", "3.0.0", {}))
    corpus.add_prediction("case-1", "retention_heuristic", 0.8, 0.35)
    assert corpus.performance_report()["metrics"]["retention_heuristic"]["status"] == "insufficient_samples"
