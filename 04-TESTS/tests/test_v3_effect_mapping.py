import shutil
import subprocess

import pytest

from ai_video_factory.effects_engine import build_cinematic_filter
from ai_video_factory import render_engine


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


def test_trimmed_clips_are_rendered_from_zero_offset(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(render_engine, "choose_encoder", lambda: "libx264")
    monkeypatch.setattr(render_engine, "run_ffmpeg", lambda command, **_: commands.append(command))
    monkeypatch.setattr(render_engine, "validate_media_output", lambda _: {"format": {"duration": 2.0}})

    trimmed = tmp_path / "_clips" / "clip_000.mp4"
    trimmed.parent.mkdir()
    trimmed.write_bytes(b"placeholder")
    render_engine.render_segment(str(trimmed), ss=17.5, duration=2.0, vf="null", dst=str(tmp_path / "out.mp4"))

    assert commands
    assert commands[0][commands[0].index("-ss") + 1] == "0.0"


@pytest.mark.integration
def test_v3_motion_filter_executes_in_ffmpeg(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is unavailable")
    source = tmp_path / "source.mp4"
    output = tmp_path / "output.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
            "-t", "1", "-c:v", "libx264", str(source),
        ],
        check=True,
    )
    graph = build_cinematic_filter(1, "Climax [tracking]", 1.0, target_size=(360, 640))
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), "-vf", graph, "-t", "0.5", "-f", "null", "-"],
        check=True,
    )


def test_v3_unknown_directive_does_not_corrupt_filter_chain():
    filter_graph = build_cinematic_filter(0, "Hook [future-effect]", 2.0)
    assert filter_graph.startswith("scale=")
    assert "future-effect" not in filter_graph