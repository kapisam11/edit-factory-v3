"""Provider-neutral AI response validation and failure classification."""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

class AIResponseError(ValueError):
    pass

def bounded_text(value: Any, max_chars: int) -> str:
    text = str(value or '').strip()
    if len(text) > int(max_chars):
        raise AIResponseError(f'text exceeds {max_chars} characters')
    return text

def parse_json_object(raw: str, max_chars: int = 20000) -> dict[str, Any]:
    text = bounded_text(raw, max_chars)
    if text.startswith('```'):
        parts = text.splitlines()
        if parts and parts[0].strip().startswith('```'): parts = parts[1:]
        if parts and parts[-1].strip() == '```': parts = parts[:-1]
        text = '\n'.join(parts).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIResponseError('model output is not valid JSON') from exc
    if not isinstance(payload, dict):
        raise AIResponseError('model output must be a JSON object')
    return payload

def validate_exact_keys(payload: Mapping[str, Any], required: Iterable[str], optional: Iterable[str] = ()) -> dict[str, Any]:
    required_keys = set(required)
    allowed_keys = required_keys | set(optional)
    actual = set(payload)
    if required_keys - actual:
        raise AIResponseError(f'model output is missing keys: {sorted(required_keys - actual)}')
    if actual - allowed_keys:
        raise AIResponseError(f'model output contains unexpected keys: {sorted(actual - allowed_keys)}')
    return dict(payload)

def validate_string_fields(payload: Mapping[str, Any], fields: Iterable[str], max_chars: int = 500) -> dict[str, Any]:
    result = dict(payload)
    for field in fields:
        value = result.get(field)
        if not isinstance(value, str) or not value.strip():
            raise AIResponseError(f"model field '{field}' must be a non-empty string")
        if len(value) > int(max_chars):
            raise AIResponseError(f"model field '{field}' exceeds {max_chars} characters")
    return result

def validate_confidence(value: Any) -> float:
    try: confidence = float(value)
    except (TypeError, ValueError) as exc: raise AIResponseError('model confidence must be numeric') from exc
    if not 0.0 <= confidence <= 1.0: raise AIResponseError('model confidence must be between 0 and 1')
    return confidence

def reject_prompt_injection(text: str) -> bool:
    value = str(text or '').lower()
    markers = ('ignore previous instructions', 'ignore all instructions', 'system message', 'developer message', 'reveal the prompt', 'api key')
    return any(marker in value for marker in markers)

def classify_api_failure(error: str | BaseException) -> str:
    text = str(error).lower()
    if any(marker in text for marker in ('401','403','invalid api key','authentication')): return 'authentication'
    if any(marker in text for marker in ('429','rate limit','quota')): return 'rate_limit'
    if any(marker in text for marker in ('timeout','timed out','502','503','504','connection')): return 'transient'
    if any(marker in text for marker in ('json','schema','validation','malformed')): return 'invalid_output'
    return 'unknown'

__all__ = ['AIResponseError','bounded_text','parse_json_object','validate_exact_keys','validate_string_fields','validate_confidence','reject_prompt_injection','classify_api_failure']