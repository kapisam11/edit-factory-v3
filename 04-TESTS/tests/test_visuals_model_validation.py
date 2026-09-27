from __future__ import annotations

from ai_video_factory import visuals_fetcher


def test_model_visual_output_is_schema_validated(monkeypatch):
    visuals = [{"url": "https://example.test/a", "title": "A"}, {"url": "https://example.test/b", "title": "B"}]
    summary = {"topic": "topic"}
    monkeypatch.setattr(visuals_fetcher, "call_model", lambda *args, **kwargs: '[{"url":"https://example.test/a","purpose":"hook","confidence":0.9,"extra":true}]')
    result = visuals_fetcher.vet_with_model(visuals, summary, "key")
    assert result[0].get("purpose") == "context" or result[0].get("purpose") is not None
    assert "model_confidence" not in result[0]


def test_model_visual_output_rejects_unknown_url_and_confidence(monkeypatch):
    visuals = [{"url": "https://example.test/a", "title": "A"}]
    summary = {"topic": "topic"}
    monkeypatch.setattr(visuals_fetcher, "call_model", lambda *args, **kwargs: '[{"url":"https://evil.test/x","purpose":"hook","confidence":2}]')
    result = visuals_fetcher.vet_with_model(visuals, summary, "key")
    assert result == visuals