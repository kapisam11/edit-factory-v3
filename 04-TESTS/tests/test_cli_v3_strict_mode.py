from __future__ import annotations

import argparse
import logging

import pytest

from cli_v3 import _enforce_strict_optional_failures, _warn_if_cli_secrets


def test_cli_secret_warning_never_echoes_secret(caplog: pytest.LogCaptureFixture) -> None:
    secret = "not-for-logs-test-secret"
    args = argparse.Namespace(
        groq_key=secret,
        model_key=None,
        elevenlabs_key=None,
    )

    with caplog.at_level(logging.WARNING, logger="edit_factory_v3.cli"):
        _warn_if_cli_secrets(args)

    assert "may be exposed in shell history" in caplog.text
    assert secret not in caplog.text


def test_strict_mode_fails_for_optional_stage_failure() -> None:
    with pytest.raises(SystemExit) as exc:
        _enforce_strict_optional_failures(
            True, [("subtitle_burn", "synthetic failure")]
        )

    assert exc.value.code == 2


def test_non_strict_mode_keeps_optional_stage_failure_nonfatal() -> None:
    _enforce_strict_optional_failures(
        False, [("subtitle_burn", "synthetic failure")]
    )


def test_strict_mode_succeeds_when_optional_stages_succeed() -> None:
    _enforce_strict_optional_failures(True, [])
