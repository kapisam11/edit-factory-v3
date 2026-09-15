from ai_video_factory.effects_engine import build_cinematic_filter


def test_v3_motion_directives_map_to_real_ffmpeg_effects():
    base = build_cinematic_filter(0, "Hook", 2.0)
    zoom = build_cinematic_filter(0, "Hook [punch-in]", 2.0)
    move = build_cinematic_filter(1, "Climax [tracking]", 2.0)
    nostalgia = build_cinematic_filter(2, "Memory [subtle-parallax] [dissolve]", 2.0)

    assert len(zoom) > len(base)
    assert "zoompan" in zoom
    assert "pan=" in move
    assert "gamma=1.04" in nostalgia
    assert "fade=t=in" in nostalgia


def test_v3_unknown_directive_does_not_corrupt_filter_chain():
    filter_graph = build_cinematic_filter(0, "Hook [future-effect]", 2.0)
    assert filter_graph.startswith("scale=")
    assert "future-effect" not in filter_graph
