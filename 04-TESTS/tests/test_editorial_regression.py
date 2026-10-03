from ai_video_factory.editorial_regression import compare_editorial_metrics

def test_creative_regression_passes_within_tolerance():
    result=compare_editorial_metrics({"overall":4.0},{"overall":3.97})
    assert result.passed

def test_creative_regression_fails_on_real_drop():
    result=compare_editorial_metrics({"overall":4.0},{"overall":3.8})
    assert not result.passed
    assert "overall" in result.regressions
