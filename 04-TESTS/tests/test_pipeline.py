"""Tests for the pipeline orchestration primitives."""
import json
import os
import tempfile

import pytest

from ai_video_factory.pipeline import (
    PipelineError,
    PipelineManifest,
    Severity,
    StepResult,
    run_step,
)


class TestStepResult:
    def test_success_factory(self):
        r = StepResult.success(output=42, notes=["done"])
        assert r.ok is True
        assert r.output == 42
        assert r.severity == Severity.OPTIONAL

    def test_critical_factory(self):
        r = StepResult.critical(notes=["boom"])
        assert r.ok is False
        assert r.failed_critical is True
        assert r.failed_degraded is False

    def test_degraded_factory(self):
        r = StepResult.degraded(output=None, notes=["meh"])
        assert r.ok is False
        assert r.failed_critical is False
        assert r.failed_degraded is True

    def test_optional_failure_is_neither(self):
        r = StepResult.optional(notes=["shrug"])
        assert r.ok is False
        assert r.failed_critical is False
        assert r.failed_degraded is False


class TestRunStep:
    def test_successful_step(self):
        def add(a, b):
            return a + b

        result = run_step("add", add, Severity.CRITICAL, 2, 3)
        assert result.ok is True
        assert result.output == 5

    def test_failed_critical_step(self):
        def boom():
            raise RuntimeError("kaboom")

        result = run_step("boom", boom, Severity.CRITICAL)
        assert result.ok is False
        assert result.severity == Severity.CRITICAL
        assert "kaboom" in result.notes[0]

    def test_failed_optional_step(self):
        def meh():
            raise ValueError("whatever")

        result = run_step("meh", meh, Severity.OPTIONAL)
        assert result.ok is False
        assert result.severity == Severity.OPTIONAL


class TestPipelineManifest:
    def test_record_and_degraded_flag(self):
        m = PipelineManifest(topic="test", target_seconds=45.0)
        m.record("good", StepResult.success())
        assert m.degraded is False

        m.record("bad", StepResult.degraded())
        assert m.degraded is True

    def test_critical_failure_flag(self):
        m = PipelineManifest(topic="test")
        m.record("fatal", StepResult.critical())
        assert m.critical_failure == "fatal"

    def test_to_dict_roundtrip(self):
        m = PipelineManifest(topic="Minecraft", target_seconds=30.0)
        m.record("research", StepResult.success(output={"a": 1}))
        m.record("music", StepResult.optional(notes=["no key"]))
        m.add_artifact("package_dir", "/tmp/pkg")

        d = m.to_dict()
        assert d["topic"] == "Minecraft"
        assert d["target_seconds"] == 30.0
        assert d["steps"]["research"]["ok"] is True
        assert d["steps"]["music"]["severity"] == "optional"
        assert d["artifacts"]["package_dir"] == "/tmp/pkg"

    def test_manifest_serialization(self, tmp_path):
        m = PipelineManifest(topic="x")
        m.record("s1", StepResult.success())
        path = tmp_path / "manifest.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(m.to_dict(), f)
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["topic"] == "x"


class TestPipelineError:
    def test_raise_and_catch(self):
        r = StepResult.critical(notes=["network down"])
        with pytest.raises(PipelineError) as exc_info:
            raise PipelineError(r, "research")
        assert "research" in str(exc_info.value)
        assert "network down" in str(exc_info.value)
