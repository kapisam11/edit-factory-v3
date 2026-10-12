from __future__ import annotations

import pytest

from app.runtime_secrets import require_runtime_secret


def test_runtime_secret_rejects_missing_values_without_echoing_value():
    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY"):
        require_runtime_secret("FLASK_SECRET_KEY", "", environment="development")


@pytest.mark.parametrize(
    "placeholder",
    [
        "dev-secret-change-me",
        "dev-token-change-me",
        "replace-me",
        "your-secret-key",
        "placeholder",
        "a-short-secret",
    ],
)
def test_runtime_secret_rejects_placeholders_and_short_values(placeholder):
    with pytest.raises(RuntimeError) as error:
        require_runtime_secret("AIVF_DASHBOARD_TOKEN", placeholder, environment="development")
    assert placeholder not in str(error.value)


def test_runtime_secret_accepts_generated_length_values():
    secret = "a" * 64
    assert require_runtime_secret("FLASK_SECRET_KEY", secret, environment="production") == secret


def test_short_secret_is_allowed_only_when_test_environment_is_explicit():
    assert require_runtime_secret(
        "AIVF_DASHBOARD_TOKEN", "test-token", environment="test"
    ) == "test-token"
    with pytest.raises(RuntimeError):
        require_runtime_secret(
            "AIVF_DASHBOARD_TOKEN", "test-token", environment="development"
        )


def test_test_environment_still_requires_nonempty_secrets():
    with pytest.raises(RuntimeError, match="AIVF_DASHBOARD_TOKEN"):
        require_runtime_secret("AIVF_DASHBOARD_TOKEN", None, environment="test")
