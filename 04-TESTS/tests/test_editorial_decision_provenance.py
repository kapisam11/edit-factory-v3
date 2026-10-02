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
