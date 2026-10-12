from __future__ import annotations

import pytest

from ai_video_factory.security_config import validate_dashboard_secrets


def test_dashboard_secrets_require_explicit_distinct_strong_values() -> None:
    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY must be explicitly configured"):
        validate_dashboard_secrets({"AIVF_ENV": "development", "AIVF_DASHBOARD_TOKEN": "x" * 40})

    with pytest.raises(RuntimeError, match="AIVF_DASHBOARD_TOKEN must be explicitly configured"):
        validate_dashboard_secrets({"AIVF_ENV": "production", "FLASK_SECRET_KEY": "f" * 40})

    with pytest.raises(RuntimeError, match="development placeholder"):
        validate_dashboard_secrets({
            "AIVF_ENV": "development",
            "FLASK_SECRET_KEY": "dev-secret-change-me",
            "AIVF_DASHBOARD_TOKEN": "t" * 40,
        })

    with pytest.raises(RuntimeError, match="at least 32 characters"):
        validate_dashboard_secrets({
            "AIVF_ENV": "production",
            "FLASK_SECRET_KEY": "short",
            "AIVF_DASHBOARD_TOKEN": "t" * 40,
        })

    with pytest.raises(RuntimeError, match="must be different"):
        validate_dashboard_secrets({
            "AIVF_ENV": "production",
            "FLASK_SECRET_KEY": "x" * 40,
            "AIVF_DASHBOARD_TOKEN": "x" * 40,
        })


def test_explicit_test_environment_can_use_test_credentials() -> None:
    validate_dashboard_secrets({"AIVF_ENV": "test"})
    validate_dashboard_secrets({
        "AIVF_ENV": "test",
        "FLASK_SECRET_KEY": "test",
        "AIVF_DASHBOARD_TOKEN": "test",
    })
