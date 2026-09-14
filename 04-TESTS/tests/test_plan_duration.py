import pytest

from ai_video_factory.plan import make_idea


def _summary(seconds):
    return {
        "topic": "test",
        "target_total_seconds": seconds,
        "strongest_angle": "A strong angle",
        "main_conflict": "A conflict appears",
        "why_care": "It matters",
    }


def test_plan_respects_minimum_duration():
    idea = make_idea(_summary(15))
    assert idea["structure"]["total_seconds"] == 15.0


def test_plan_respects_maximum_duration():
    idea = make_idea(_summary(120))
    assert idea["structure"]["total_seconds"] == 120.0


def test_plan_rejects_out_of_range_duration():
    with pytest.raises(ValueError):
        make_idea(_summary(5))
    with pytest.raises(ValueError):
        make_idea(_summary(121))
