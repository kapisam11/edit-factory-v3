"""Fail-fast validation for dashboard runtime secrets."""

from __future__ import annotations

_PLACEHOLDER_VALUES = {
    "dev-secret-change-me",
    "dev-token-change-me",
    "change-me",
    "changeme",
    "placeholder",
    "replace-me",
    "your-secret-key",
    "your-secret-here",
    "your-dashboard-token",
    "local-development",
    "secret",
    "password",
}


def require_runtime_secret(name: str, value: str | None, *, environment: str) -> str:
    """Return a configured secret or fail without echoing any part of it.

    Short/test-only keys are accepted only when the operator explicitly sets
    AIVF_ENV=test (or testing). Normal local-development and production runs
    require at least 32 characters and reject common placeholder values.
    """
    secret = (value or "").strip()
    if not secret:
        raise RuntimeError(
            f"{name} must be set before the dashboard starts; configure it in the local .env or secret manager."
        )

    if str(environment).strip().lower() in {"test", "testing"}:
        return secret

    lowered = secret.lower()
    placeholder = (
        lowered in _PLACEHOLDER_VALUES
        or any(marker in lowered for marker in ("change-me", "changeme", "placeholder", "replace-me", "your-secret", "your-token"))
    )
    if placeholder:
        raise RuntimeError(
            f"{name} contains a known placeholder value; generate a new secret before starting the dashboard."
        )
    if len(secret) < 32:
        raise RuntimeError(
            f"{name} must contain at least 32 characters outside the explicit test environment."
        )
    return secret


__all__ = ["require_runtime_secret"]
