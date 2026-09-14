"""Provider-safe model adapter with structured failure reporting."""
import os
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


def _extract_chat_content(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    return str((choices[0].get("message") or {}).get("content") or "")


def _validate_provider_key(provider: str, key: str) -> None:
    """Reject obvious cross-provider credential mixups before making a request."""
    if provider == "groq" and key.startswith("sk-"):
        raise ValueError("API key appears to belong to openai, not groq")
    if provider == "openai" and key.startswith("gsk_"):
        raise ValueError("API key appears to belong to groq, not openai")


def _call_openai(prompt: str, key: str, timeout: int) -> str:
    import requests
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": os.environ.get("AIVF_OPENAI_MODEL", "gpt-4o-mini"),
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.7, "max_tokens": 800},
        timeout=timeout,
    )
    response.raise_for_status()
    return _extract_chat_content(response.json())


def _call_groq(prompt: str, key: str, timeout: int) -> str:
    import requests
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": os.environ.get("AIVF_GROQ_MODEL", "llama-3.3-70b-versatile"),
              "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.7, "max_tokens": 800},
        timeout=timeout,
    )
    response.raise_for_status()
    return _extract_chat_content(response.json())


def call_model_result(prompt: str, api_key: Optional[str] = None, timeout: int = 15,
                      provider: Optional[str] = None) -> ModelResult:
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    selected = (provider or "").strip().lower()
    if api_key:
        key = api_key
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
        return ModelResult(success=False, error_type="not_configured", message="No model provider is configured")

    if selected not in {"openai", "groq"}:
        raise ValueError(f"Unsupported model provider: {selected}")

    if api_key:
        _validate_provider_key(selected, key)

    try:
        text = _call_groq(prompt, key, timeout) if selected == "groq" else _call_openai(prompt, key, timeout)
        if not text:
            return ModelResult(success=False, provider=selected, error_type="empty_response",
                               message="Provider returned no text")
        return ModelResult(success=True, text=text, provider=selected)
    except Exception as exc:
        return ModelResult(success=False, provider=selected, error_type=type(exc).__name__,
                           message=f"{selected} model request failed")


def call_model(prompt: str, api_key: Optional[str] = None, timeout: int = 15,
               provider: Optional[str] = None) -> str:
    result = call_model_result(prompt, api_key=api_key, timeout=timeout, provider=provider)
    if result.success:
        return result.text
    if result.error_type == "not_configured":
        return ""
    raise ModelCallError(result.message or "model request failed")
