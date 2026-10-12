"""Fail-closed validation for dashboard secrets at the production entrypoint."""
from __future__ import annotations

import os
import re
from collections.abc import Mapping


_INSECURE_EXACT_VALUES = {
    "dev-secret-change-me",
    "dev-token-change-me",
    "change-me",
    "changeme",
    "replace-me",
    "secret",
    "password",
    "test",
}


def validate_dashboard_secrets(environ: Mapping[str, str] | None = None) -> None:
    """Require distinct, non-placeholder secrets unless explicitly running tests.

    The development Compose profile may use HTTP/loopback, but it must still use
    generated local secrets rather than built-in public fallback credentials.
    Only AIVF_ENV=test permits absent/short test credentials.
    """
    values = os.environ if environ is None else environ
    environment = str(values.get("AIVF_ENV", "production")).strip().lower()
    if environment == "test":
        return

    required = (
        ("FLASK_SECRET_KEY", str(values.get("FLASK_SECRET_KEY", "")).strip()),
        ("AIVF_DASHBOARD_TOKEN", str(values.get("AIVF_DASHBOARD_TOKEN", "")).strip()),
    )
    for name, value in required:
        if not value:
            raise RuntimeError(f"{name} must be explicitly configured; refusing to start the dashboard.")
        lowered = value.casefold()
        if lowered in _INSECURE_EXACT_VALUES or re.search(
            r"(?:dev|development)[-_ ]?(?:secret|token)|change[-_ ]?me|replace[-_ ]?me|placeholder",
            lowered,
        ):
            raise RuntimeError(f"{name} uses a known development placeholder; generate a new secret.")
        if len(value) < 32:
            raise RuntimeError(f"{name} must contain at least 32 characters.")

    if required[0][1] == required[1][1]:
        raise RuntimeError("FLASK_SECRET_KEY and AIVF_DASHBOARD_TOKEN must be different values.")


__all__ = ["validate_dashboard_secrets"]
