import json

from ai_video_factory.editorial_evaluation import (
    EditorialEvaluation,
    Evidence,
    EvidenceKind,
    EvaluationCase,
    build_retention_decisions,
    confidence_action,
    decision_id,
)
from ai_video_factory.feedback_store import FeedbackStore
from ai_video_factory.idempotency import is_cache_hit, stage_cache_key
from ai_video_factory.v3_engine import V3Config, create_v3_blueprint


def test_blueprint_contains_explainable_editorial_decisions():
    blueprint = create_v3_blueprint("Minecraft clutch", config=V3Config(target_seconds=8))
    assert blueprint.editorial_decisions
    assert all(decision.reason for decision in blueprint.editorial_decisions)
    assert all(decision.evidence for decision in blueprint.editorial_decisions)
    assert all(item.kind == EvidenceKind.HEURISTIC.value for decision in blueprint.editorial_decisions for item in decision.evidence)
    payload = blueprint.to_dict()
    restored = blueprint.from_dict(json.loads(json.dumps(payload)))
    assert len(restored.editorial_decisions) == len(blueprint.editorial_decisions)


def test_low_confidence_decisions_choose_conservative_action():
    decisions = build_retention_decisions([{"time": 1.0, "kind": "unknown", "instruction": "preserve story"}])
    assert decisions[0].operation == "DO_NOTHING"
    assert confidence_action(decisions[0].confidence) == "conservative"


def test_decision_ids_are_stable():
    decision = build_retention_decisions([{"time": 1.0, "kind": "text", "instruction": "support comprehension"}])[0]
    assert decision_id(decision) == decision_id(decision)


def test_evaluation_and_feedback_store_capture_human_signal(tmp_path):
    case = EvaluationCase("case-1", "source.mp4", "gaming", 8.0)
    evaluation = EditorialEvaluation(
        4, 3.5, 4.5, 3, 4, 3.8,
        reviewer="editor",
        notes="payoff needs more breathing room",
        evaluated_at="2026-10-02T18:00:00Z",
        case_id=case.case_id,
    )
    store = FeedbackStore(tmp_path / "feedback.sqlite")
    store.record_editorial_evaluation(evaluation, metadata={"case": case.to_dict()})
    summary = store.editorial_summary()
    assert summary["samples"] == 1
    assert summary["means"]["overall"] == 3.8


def test_stage_cache_key_changes_when_configuration_changes(tmp_path):
    key_a = stage_cache_key(source_hash="abc", stage_name="planning", stage_version="1", configuration={"bpm": 120})
    key_b = stage_cache_key(source_hash="abc", stage_name="planning", stage_version="1", configuration={"bpm": 121})
    assert key_a != key_b
    output = tmp_path / "result.json"
    output.write_text("{}", encoding="utf-8")
    record = {"key": key_a, "outputs": ["result.json"]}
    assert is_cache_hit(record, expected_key=key_a, package=tmp_path)
    assert not is_cache_hit(record, expected_key=key_b, package=tmp_path)
