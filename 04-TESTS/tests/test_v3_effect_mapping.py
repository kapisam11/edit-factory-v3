import pytest

from ai_video_factory.effects_engine import build_cinematic_filter


def test_v3_motion_directives_map_to_real_ffmpeg_video_effects():
    base = build_cinematic_filter(0, "Hook", 2.0)
    zoom = build_cinematic_filter(0, "Hook [punch-in]", 2.0)
    move = build_cinematic_filter(1, "Climax [tracking]", 2.0)
    nostalgia = build_cinematic_filter(2, "Memory [subtle-parallax] [dissolve]", 2.0)

    assert len(zoom) > len(base)
    assert "crop=" in zoom
    assert "zoompan" not in zoom
    assert "crop=" in move
    assert "pan=" not in move
    assert "gamma=1.04" in nostalgia
    assert "fade=t=in" in nostalgia


def test_v3_filter_is_valid_for_all_platform_sizes():
    for size in ((1080, 1920), (1080, 1080), (1920, 1080)):
        graph = build_cinematic_filter(0, "Hook [tracking]", 2.0, target_size=size)
        assert f"crop=w={size[0]}:h={size[1]}" in graph


def test_v3_filter_rejects_invalid_inputs():
    with pytest.raises(ValueError):
        build_cinematic_filter(0, "Hook", 0.0)
    with pytest.raises(ValueError):
        build_cinematic_filter(0, "Hook", 2.0, target_size=(0, 1920))


def test_v3_unknown_directive_does_not_corrupt_filter_chain():
    filter_graph = build_cinematic_filter(0, "Hook [future-effect]", 2.0)
    assert filter_graph.startswith("scale=")
    assert "future-effect" not in filter_graph
