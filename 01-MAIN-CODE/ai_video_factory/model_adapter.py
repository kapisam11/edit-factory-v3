"""Provider-safe model adapter with bounded retries and structured failures."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional


class ModelCallError(RuntimeError):
    """Raised when a configured model provider cannot complete a request."""


@dataclass(frozen=True)
class ModelResult:
    success: bool
    text: str = ""
    provider: str = ""
    error_type: Optional[str] = None
    message: Optional[str] = None
    attempts: int = 0
    retryable: bool = False


def _extract_chat_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return ""
    content = (choices[0].get("message") or {}).get("content")
    return content if isinstance(content, str) else str(content or "")


def _validate_provider_key(provider: str, key: str) -> None:
    if provider == "groq" and key.startswith("sk-"):
        raise ValueError("API key appears to belong to openai, not groq")
    if provider == "openai" and key.startswith("gsk_"):
        raise ValueError("API key appears to belong to groq, not openai")


def _request_chat(url: str, key: str, model: str, prompt: str, timeout: int) -> str:
    import requests

    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.7, "max_tokens": 800},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise _RetryableModelError(f"model request transport failure: {type(exc).__name__}") from exc
    status = response.status_code
    if status == 429 or 500 <= status <= 599:
        raise _RetryableModelError(f"provider returned retryable HTTP {status}")
    if status >= 400:
        raise ModelCallError(f"provider returned HTTP {status}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ModelCallError("provider returned invalid JSON") from exc
    return _extract_chat_content(payload)


class _RetryableModelError(ModelCallError):
    """Transient provider failure eligible for bounded retry."""


def _call_with_retry(prompt: str, key: str, provider: str, timeout: int, max_retries: int = 2) -> ModelResult:
    endpoints = {
        "openai": ("https://api.openai.com/v1/chat/completions", os.environ.get("AIVF_OPENAI_MODEL", "gpt-4o-mini")),
        "groq": ("https://api.groq.com/openai/v1/chat/completions", os.environ.get("AIVF_GROQ_MODEL", "llama-3.3-70b-versatile")),
    }
    url, model = endpoints[provider]
    attempts = 0
    for attempt in range(max_retries + 1):
        attempts = attempt + 1
        try:
            text = _request_chat(url, key, model, prompt, timeout)
            if not text.strip():
                return ModelResult(False, provider=provider, error_type="empty_response", message="Provider returned no text", attempts=attempts)
            return ModelResult(True, text=text, provider=provider, attempts=attempts)
        except _RetryableModelError as exc:
            if attempt >= max_retries:
                return ModelResult(False, provider=provider, error_type=type(exc).__name__, message="model provider remained unavailable after bounded retries", attempts=attempts, retryable=True)
            time.sleep(min(2 ** attempt, 4))
        except ModelCallError as exc:
            return ModelResult(False, provider=provider, error_type=type(exc).__name__, message=str(exc), attempts=attempts, retryable=False)
    return ModelResult(False, provider=provider, error_type="unknown", message="model provider failed", attempts=attempts)


def call_model_result(prompt: str, api_key: Optional[str] = None, timeout: int = 15,
                      provider: Optional[str] = None) -> ModelResult:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if len(prompt) > 100_000:
        raise ValueError("prompt is too large")
    if not 1 <= int(timeout) <= 300:
        raise ValueError("timeout must be between 1 and 300 seconds")

    selected = (provider or "").strip().lower()
    if api_key:
        key = str(api_key).strip()
        selected = selected or ("groq" if key.startswith("gsk_") else "openai")
    elif selected == "groq":
        key = os.environ.get("GROQ_API_KEY", "")
    elif selected == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
    elif os.environ.get("OPENAI_API_KEY"):
        selected, key = "openai", os.environ["OPENAI_API_KEY"]
    elif os.environ.get("GROQ_API_KEY"):
        selected, key = "groq", os.environ["GROQ_API_KEY"]
    else:
        return ModelResult(False, error_type="not_configured", message="No model provider is configured")

    if selected not in {"openai", "groq"}:
        raise ValueError(f"Unsupported model provider: {selected}")
    if not key:
        return ModelResult(False, provider=selected, error_type="not_configured", message=f"{selected} provider is not configured")
    _validate_provider_key(selected, key)
    return _call_with_retry(prompt, key, selected, int(timeout))


def call_model(prompt: str, api_key: Optional[str] = None, timeout: int = 15,
               provider: Optional[str] = None) -> str:
    result = call_model_result(prompt, api_key=api_key, timeout=timeout, provider=provider)
    if result.success:
        return result.text
    # Model-backed editing must degrade to the deterministic planner on transient provider failures.
    if result.error_type in {"not_configured", "empty_response", "_RetryableModelError"} or result.retryable:
        return ""
    raise ModelCallError(result.message or "model request failed")
