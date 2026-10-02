from ai_video_factory.v3_performance import analyze_stage_timings


def test_stage_timing_report_ranks_bottleneck_and_shares():
    report = analyze_stage_timings({
        "planning": 100,
        "render": 900,
        "qc": 200,
    })
    assert report.total_ms == 1200
    assert report.bottleneck == "render"
    assert report.stages[0].name == "render"
    assert sum(item.share for item in report.stages) == 1.0
    assert report.stages[0].share > report.stages[1].share


def test_stage_timing_rejects_negative_values():
    try:
        analyze_stage_timings({"render": -1})
    except ValueError as exc:
        assert "negative" in str(exc)
    else:
        raise AssertionError("negative stage timing was accepted")
