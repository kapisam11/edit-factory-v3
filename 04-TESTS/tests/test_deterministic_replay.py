from ai_video_factory.deterministic_replay import assert_deterministic_replay

def test_v3_decision_graph_is_deterministic():
    digest=assert_deterministic_replay("Minecraft clutch", config=None)
    assert len(digest)==64
