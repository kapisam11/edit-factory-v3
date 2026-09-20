from ai_video_factory.v3_capabilities import CAPABILITIES, validate_capabilities


def test_v3_capability_registry_is_complete_and_evidenced():
    validate_capabilities()
    assert len(CAPABILITIES) == 40
    assert all(spec.status in {"implemented", "hybrid", "heuristic"} for spec in CAPABILITIES.values())
    assert all(spec.evidence_level and spec.display_name and spec.truth_note for spec in CAPABILITIES.values())
    assert all(spec.implementation and spec.validator and spec.tests for spec in CAPABILITIES.values())
