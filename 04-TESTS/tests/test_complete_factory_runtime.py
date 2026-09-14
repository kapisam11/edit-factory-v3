from pathlib import Path

from ai_video_factory.complete_factory import PLATFORM_LAYOUTS, render_all_platforms


def test_platform_layouts_cover_requested_formats():
    assert PLATFORM_LAYOUTS["youtube_shorts"] == (1080, 1920)
    assert PLATFORM_LAYOUTS["youtube"] == (1920, 1080)
    assert PLATFORM_LAYOUTS["square"] == (1080, 1080)


def test_runtime_module_is_importable():
    from ai_video_factory.complete_factory import run_complete_factory, run_autonomous_queue

    assert callable(run_complete_factory)
    assert callable(run_autonomous_queue)


def test_render_all_platforms_requires_real_ffmpeg_input(tmp_path: Path):
    missing = tmp_path / "missing.mp4"
    try:
        render_all_platforms(str(missing), str(tmp_path), ["square"])
    except Exception as exc:
        assert "ffmpeg" in str(exc).lower() or "no such file" in str(exc).lower() or "failed" in str(exc).lower()
    else:
        raise AssertionError("rendering a missing source should fail")
