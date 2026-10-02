from ai_video_factory.editorial_evaluation import Assessment, Evidence, EvidenceKind, build_retention_decisions

def test_assessment_keeps_quality_and_confidence_separate():
    evidence = Evidence(EvidenceKind.HEURISTIC, "test", 0.4, "1.0.0")
    assessment = Assessment(0.9, 0.4, (evidence,), "test", "1.0.0")
    assert assessment.value == 0.9
    assert assessment.confidence == 0.4

def test_do_nothing_is_explicit_for_low_confidence():
    decisions = build_retention_decisions([{"time": 2.0, "kind": "zoom", "confidence": 0.2, "reason": "LOW_VALUE"}])
    assert decisions[0].operation == "DO_NOTHING"
    assert decisions[0].reason == "LOW_VALUE"


def test_editorial_provenance_survives_blueprint_round_trip():
    from ai_video_factory.v3_engine import V3Config, create_v3_blueprint

    blueprint = create_v3_blueprint("Minecraft clutch", config=V3Config(target_seconds=8))
    restored = blueprint.from_dict(blueprint.to_dict())

    assert restored.editorial_decisions == blueprint.editorial_decisions
    assert restored.editorial_decisions[0].policy_version == "2.0.0-semantic"
    assert restored.editorial_decisions[0].config_hash
