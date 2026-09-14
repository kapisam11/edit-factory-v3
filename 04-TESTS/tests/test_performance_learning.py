from ai_video_factory.performance_learning import ChannelPerformanceModel, PerformanceObservation


def obs(i, retention=.7, completion=.6, rewatch=.1, share=.04, save=.04, edit="Storytelling"):
    return PerformanceObservation(f"v{i}", .8, retention, completion, rewatch, share, save, edit)


def test_learning_waits_for_evidence():
    model = ChannelPerformanceModel()
    model.add(obs(1))
    assert model.recommend().priority == "collect-data"


def test_learning_produces_bounded_directional_recommendations():
    model = ChannelPerformanceModel()
    model.extend([obs(1, .4, .3, .03, .01, .01), obs(2, .45, .35, .04, .01, .01), obs(3, .5, .4, .05, .02, .01)])
    recommendation = model.recommend()
    assert 0 <= recommendation.confidence <= .95
    assert recommendation.evidence_count == 3
    assert recommendation.changes


def test_edit_type_lift_is_comparable():
    model = ChannelPerformanceModel()
    model.extend([obs(1, edit="Funny"), obs(2, edit="Funny"), obs(3, retention=.4, completion=.3, edit="Storytelling")])
    lifts = model.edit_type_lift()
    assert set(lifts) == {"Funny", "Storytelling"}
    assert lifts["Funny"] > lifts["Storytelling"]
