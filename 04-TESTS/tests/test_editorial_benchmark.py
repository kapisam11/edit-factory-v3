from ai_video_factory.editorial_benchmark import build_correlation_report, spearman_rho

def test_spearman_perfect_positive():
    assert spearman_rho([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0

def test_spearman_perfect_negative():
    assert spearman_rho([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0

def test_report_refuses_correlation_with_insufficient_samples():
    report = build_correlation_report({"case-1": {"hook": 4.0, "overall": 4.0}}, [])
    assert report["dimensions"]["hook"]["spearman_rho"] is None
    assert report["dimensions"]["hook"]["status"] == "insufficient_samples"
