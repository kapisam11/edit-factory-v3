import json

from ai_video_factory.plan import make_idea
from ai_video_factory.quality_control import run_final_checks
from ai_video_factory.story import enforce_story_arc
from tools.enforce_hook import normalize_hook_length, ensure_hook_first


def test_make_idea_starts_with_hook_first_opening():
    summary = {
        "topic": "Test topic",
        "emotion": "dramatic",
        "strongest_angle": "His final choice",
        "main_conflict": "The decision changed everything overnight",
        "who_what": "A rushed choice put everyone on edge",
    }
    idea = make_idea(summary)
    lines = [line.strip() for line in idea["script"].splitlines() if line.strip()]

    assert lines, "script should not be empty"
    assert 2 <= len(lines[0].split()) <= 5, "first line must be a short hook"
    assert lines[0] == idea["hook"], "first line should match the generated hook"
    assert len(lines[1].split()) <= 8, "second line should be an immediate short impact line"


def test_story_arc_preserves_hook_first_opening():
    idea = {
        "hook": "His final choice",
        "emotion": "dramatic",
        "script": "His final choice\nA big reveal happened fast and everyone reacted immediately\nThe fallout spread everywhere",
        "edit_plan": [(2.0, "Hook - impact frame")],
    }
    rewritten = enforce_story_arc(idea)
    lines = [line.strip() for line in rewritten["script"].splitlines() if line.strip()]

    assert lines[0] == "His final choice"
    assert len(lines[1].split()) <= 8


def test_make_idea_supports_60_second_structure_option():
    summary = {
        "topic": "Test topic",
        "emotion": "dramatic",
        "strongest_angle": "His final choice",
        "main_conflict": "The decision changed everything overnight",
        "who_what": "A rushed choice put everyone on edge",
        "target_total_seconds": 60,
    }
    idea = make_idea(summary)

    assert idea["structure"]["total_seconds"] == 60.0
    assert idea["structure"]["hook"] == [0.0, 2.0]
    assert idea["structure"]["intro"] == [2.0, 8.0]
    assert idea["structure"]["conflict"] == [8.0, 20.0]
    assert idea["structure"]["climax"] == [20.0, 45.0]
    assert idea["structure"]["payoff"] == [45.0, 60.0]

    total = round(sum(duration for duration, _ in idea["edit_plan"]), 2)
    assert total == 60.0
    assert idea["edit_plan"][0][1].startswith("Hook")
    assert idea["edit_plan"][1][1].startswith("Intro")
    assert idea["edit_plan"][2][1].startswith("Intro") or idea["edit_plan"][2][1].startswith("Conflict")
    assert all(1.0 <= duration <= 3.0 for duration, _ in idea["edit_plan"])


def test_make_idea_defaults_to_shorter_structure():
    summary = {
        "topic": "Test topic",
        "emotion": "dramatic",
        "strongest_angle": "His final choice",
        "main_conflict": "The decision changed everything overnight",
        "who_what": "A rushed choice put everyone on edge",
    }
    idea = make_idea(summary)

    assert idea["structure"]["total_seconds"] == 45.0
    total = round(sum(duration for duration, _ in idea["edit_plan"]), 2)
    assert total == 45.0
    assert all(1.0 <= duration <= 3.0 for duration, _ in idea["edit_plan"])


def test_hook_normalization_keeps_short_opening():
    assert normalize_hook_length("A very long opening line that is not a hook") == "A very long opening line"
    assert ensure_hook_first("Setup line\nSecond line", "Lost forever").splitlines()[0] == "Lost forever"


def test_run_final_checks_accepts_professional_plan(tmp_path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    plan = {
        "hook": "Lost forever",
        "script": "Lost forever. It happened fast and you need to know why.",
        "edit_plan": [
            [2.0, "Hook - strongest moment / jump cut / quick zoom"],
            [2.0, "Intro - topic / person / stakes / camera move"],
            [2.0, "Conflict - tension rises / motion blur / subtle shake"],
            [2.5, "Main event - turning point / speed ramp / impact frame"],
            [2.5, "Payoff - emotional ending / cinematic transition"],
        ],
    }
    with open(pkg_dir / "plan.json", "w", encoding="utf-8") as f:
        json.dump(plan, f)
    research = {"visuals": [{"purpose": "clip", "match_score": 0.9}], "trending": True}
    with open(pkg_dir / "research.json", "w", encoding="utf-8") as f:
        json.dump(research, f)
    thumb = pkg_dir / "thumbnail.png"
    thumb.write_text("dummy", encoding="utf-8")

    report = run_final_checks(str(pkg_dir))
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
    assert "scale=iw*1.04" in hook_filter
    assert "crop=1080:1920" in hook_filter
    assert "eq=contrast=1.10" in hook_filter
    assert "unsharp" in hook_filter
    assert "tblend" in hook_filter

    main_event_filter = _build_cinematic_filter(3, "Main event - turning point / speed ramp / impact frame", 2.5)
    assert "boxblur" in main_event_filter
    assert "crop=1080:1920" in main_event_filter
    assert "tblend" in main_event_filter


def test_auto_edit_varies_crop_position_by_phase():
    from ai_video_factory.auto_edit import _build_cinematic_filter

    filter_0 = _build_cinematic_filter(0, "Hook - jump cut", 2.0)
    filter_1 = _build_cinematic_filter(1, "Intro - camera move", 2.0)
    filter_2 = _build_cinematic_filter(2, "Conflict - motion blur", 2.0)
    assert "crop=1080:1920:x=0:y=3" in filter_0
    assert filter_1 != filter_2
    assert "pan" in filter_1 or "zoom" in filter_1


def test_run_final_checks_validates_shot_variety(tmp_path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    plan = {
        "hook": "Lost forever",
        "script": "Lost forever. It happened fast.",
        "edit_plan": [
            [2.0, "Hook - jump cut"],
            [2.0, "Hook - jump cut"],
            [2.0, "Intro - camera move"],
            [2.5, "Main event - speed ramp"],
            [2.5, "Payoff - cinematic transition"],
        ],
    }
    with open(pkg_dir / "plan.json", "w", encoding="utf-8") as f:
        json.dump(plan, f)
    research = {"visuals": [{"purpose": "clip", "match_score": 0.9}]}
    with open(pkg_dir / "research.json", "w", encoding="utf-8") as f:
        json.dump(research, f)
    (pkg_dir / "thumbnail.png").write_text("dummy")

    report = run_final_checks(str(pkg_dir))
    assert report["checks"]["shot_variety_ratio"] == 0.75
    assert report["checks"]["shot_variance_ok"] is True
