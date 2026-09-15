import pytest

from ai_video_factory import model_adapter


def test_groq_failure_never_falls_back_to_openai(monkeypatch):
    calls = []

    def fail_request(url, key, model, prompt, timeout):
        calls.append(url)
        raise model_adapter._RetryableModelError("provider unavailable")

    monkeypatch.setattr(model_adapter, "_request_chat", fail_request)
    monkeypatch.setattr(model_adapter.time, "sleep", lambda *_args: None)
    assert model_adapter.call_model("hello", api_key="gsk_test") == ""
    assert all("groq.com" in url for url in calls)
    assert len(calls) == 3


def test_structured_result_reports_provider_failure(monkeypatch):
    monkeypatch.setattr(
        model_adapter,
        "_request_chat",
        lambda *args, **kwargs: (_ for _ in ()).throw(model_adapter.ModelCallError("bad provider response")),
    )
    result = model_adapter.call_model_result("hello", api_key="gsk_test")
    assert result.success is False
    assert result.provider == "groq"
    assert result.error_type == "ModelCallError"
    assert result.retryable is False


def test_invalid_provider_is_rejected():
    with pytest.raises(ValueError):
        model_adapter.call_model("hello", api_key="x", provider="other")


def test_explicit_provider_rejects_mismatched_groq_key():
    with pytest.raises(ValueError, match="belong to groq"):
        model_adapter.call_model("hello", api_key="gsk_test", provider="openai")


def test_explicit_provider_rejects_mismatched_openai_key():
    with pytest.raises(ValueError, match="belong to openai"):
        model_adapter.call_model("hello", api_key="sk-test", provider="groq")


def test_opaque_key_keeps_openai_default(monkeypatch):
    called = {}

    def fake_openai(url, key, model, prompt, timeout):
        called["url"] = url
        called["key"] = key
        return "ok"

    monkeypatch.setattr(model_adapter, "_request_chat", fake_openai)
    assert model_adapter.call_model("hello", api_key="opaque-test-key") == "ok"
    assert called["key"] == "opaque-test-key"
    assert "api.openai.com" in called["url"]


def test_model_input_is_bounded():
    with pytest.raises(ValueError, match="prompt must be a non-empty"):
        model_adapter.call_model_result("   ")
    with pytest.raises(ValueError, match="too large"):
        model_adapter.call_model_result("x" * 100_001)
    with pytest.raises(ValueError, match="between 1 and 300"):
        model_adapter.call_model_result("hello", timeout=301)
