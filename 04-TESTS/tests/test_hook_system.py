    assert report["ok"] is True
    assert report["checks"]["cadence_ok"] is True
    assert report["checks"]["has_hook"] is True
    assert report["checks"]["has_main_event"] is True
    assert report["checks"]["has_payoff"] is True
    assert report["checks"]["story_beat_presence"]["Hook"] is True
    assert report["checks"]["story_beat_presence"]["Payoff"] is True
    assert report["checks"]["style_terms_present"] is True


def test_auto_edit_builds_cinematic_filters():
    from ai_video_factory.auto_edit import _build_cinematic_filter

    hook_filter = _build_cinematic_filter(0, "Hook - strongest moment / jump cut / quick zoom", 2.0)
    assert "scale=w=1176:h=2016" in hook_filter
    assert "crop=w=1080:h=1920" in hook_filter
    assert "tblend" in hook_filter
    assert "unsharp" in hook_filter

    main_event_filter = _build_cinematic_filter(3, "Main event - turning point / speed ramp / impact frame", 2.5)
    assert "boxblur" in main_event_filter
    assert "crop=w=1080:h=1920" in main_event_filter
    assert "tblend" in main_event_filter


def test_auto_edit_varies_crop_position_by_phase():
    from ai_video_factory.auto_edit import _build_cinematic_filter

    filter_0 = _build_cinematic_filter(0, "Hook - jump cut", 2.0)
    filter_1 = _build_cinematic_filter(1, "Intro - camera move", 2.0)
    filter_2 = _build_cinematic_filter(2, "Conflict - motion blur", 2.0)
    assert "crop=w=1080:h=1920" in filter_0
    assert filter_1 != filter_2
    assert "crop=w=1080:h=1920" in filter_1
    assert "crop=w=1080:h=1920" in filter_2


def test_run_final_checks_validates_shot_variety(tmp_path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    plan = {
        "hook": "Lost forever",
        "script": "Lost forever. It happened fast.",
        "edit_plan": [
            [2.0, "Hook - jump cut"],
            [2.0, "Hook - jump cut"],