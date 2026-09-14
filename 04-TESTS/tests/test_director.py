"""Tests for the VideoDirector pipeline and script generation."""
import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from ai_video_factory.director import (
    ANTI_SLOP_RULES,
    VideoDirector,
    _validate_script_against_rules,
)
from ai_video_factory.pipeline import PipelineError, StepResult


class TestValidateScriptAgainstRules:
    def test_passes_valid_script(self):
        violations = _validate_script_against_rules(
            "Lost forever.\nThis changed everything.\nAnd nobody saw it coming.",
            hook="Lost forever",
        )
        assert violations == []

    def test_fails_too_short(self):
        violations = _validate_script_against_rules("Hi.", hook="Hi")
        assert any("too short" in v for v in violations)

    def test_fails_banned_phrase(self):
        script = "Hello everyone this is the story of Minecraft. It got wild fast."
        violations = _validate_script_against_rules(script, hook="Hello everyone")
        assert any("Banned phrase" in v for v in violations)

    def test_fails_missing_punctuation(self):
        violations = _validate_script_against_rules(
            "Lost forever\nThis changed everything\nAnd nobody saw it coming",
            hook="Lost forever",
        )
        assert any("punctuation" in v for v in violations)

    def test_fails_hook_too_long(self):
        script = "This is a very long hook that should not be allowed.\nLine two.\nLine three."
        violations = _validate_script_against_rules(script, hook="This is a very long hook that should not be allowed")
        assert any("Hook too long" in v for v in violations)

    def test_fails_missing_hook(self):
        violations = _validate_script_against_rules("Some script here.", hook="")
        assert any("Missing hook" in v for v in violations)


class TestBuildScript:
    def test_template_fallback_when_no_model_key(self):
        director = VideoDirector(model_key=None)
        director.creative_brief = {
            "topic": "Test Topic",
            "hook": "The Real Reason",
            "strongest_angle": "A wild twist",
            "emotion": "dramatic",
        }
        script = director.build_script({})
        assert "The Real Reason" in script
        assert director.creative_brief["script_source"] == "template"

    def test_llm_path_parses_json_and_validates(self):
        director = VideoDirector(model_key="fake-key")
        director.creative_brief = {
            "topic": "Minecraft",
            "hook": "Lost forever",
            "strongest_angle": "Betrayal",
            "emotion": "dramatic",
        }
        director._target_seconds = 45.0

        fake_response = json.dumps({"lines": ["Lost forever", "The betrayal no one expected.", "And the ending changed everything."]})

        with patch("ai_video_factory.director.call_model", return_value=fake_response):
            script = director.build_script({})

        assert "Lost forever" in script
        assert director.creative_brief["script_source"] == "llm"
        violations = _validate_script_against_rules(script, "Lost forever")
        assert violations == []

    def test_llm_fallback_to_template_on_bad_json(self):
        director = VideoDirector(model_key="fake-key")
        director.creative_brief = {
            "topic": "Minecraft",
            "hook": "Lost forever",
            "strongest_angle": "Betrayal",
            "emotion": "dramatic",
        }
        director._target_seconds = 45.0

        with patch("ai_video_factory.director.call_model", return_value="not json"):
            script = director.build_script({})

        assert director.creative_brief["script_source"] == "template"

    def test_llm_fallback_to_template_on_rule_violation(self):
        director = VideoDirector(model_key="fake-key")
        director.creative_brief = {
            "topic": "Minecraft",
            "hook": "Lost forever",
            "strongest_angle": "Betrayal",
            "emotion": "dramatic",
        }
        director._target_seconds = 45.0

        bad_lines = ["Lost forever", "Hello everyone this is bad.", "End."]
        fake_response = json.dumps({"lines": bad_lines})

        with patch("ai_video_factory.director.call_model", return_value=fake_response):
            script = director.build_script({})

        assert director.creative_brief["script_source"] == "template"

    def test_markdown_fences_stripped(self):
        director = VideoDirector(model_key="fake-key")
        director.creative_brief = {
            "topic": "X",
            "hook": "Hook",
            "strongest_angle": "Angle",
            "emotion": "dramatic",
        }
        director._target_seconds = 45.0

        fenced = "```json\n" + json.dumps({"lines": ["Hook", "Line two.", "Line three."]}) + "\n```"
        with patch("ai_video_factory.director.call_model", return_value=fenced):
            script = director.build_script({})
        assert director.creative_brief["script_source"] == "llm"


class TestDirectorProduceErrorHandling:
    @patch("ai_video_factory.director.research_topic")
    @patch("ai_video_factory.director.deep_research")
    def test_produce_aborts_on_critical_research_failure(self, mock_deep, mock_research):
        mock_research.side_effect = RuntimeError("DNS failure")
        mock_deep.return_value = {}

        director = VideoDirector()
        with pytest.raises(PipelineError) as exc_info:
            director.produce("Minecraft", target_seconds=45.0)

        assert "research" in str(exc_info.value)

    @patch("ai_video_factory.director.create_package")
    @patch("ai_video_factory.director.research_topic")
    @patch("ai_video_factory.director.deep_research")
    def test_produce_aborts_on_critical_package_failure(self, mock_deep, mock_research, mock_pkg):
        mock_deep.return_value = {}
        mock_research.return_value = {
            "topic": "Minecraft",
            "who_what": "stuff",
            "main_conflict": "betrayal",
            "emotion": "dramatic",
            "strongest_angle": "twist",
        }
        mock_pkg.side_effect = RuntimeError("Disk full")

        director = VideoDirector()
        with pytest.raises(PipelineError) as exc_info:
            director.produce("Minecraft", target_seconds=45.0)

        assert "create_package" in str(exc_info.value)

    @patch("ai_video_factory.director.create_package")
    @patch("ai_video_factory.director.research_topic")
    @patch("ai_video_factory.director.deep_research")
    def test_produce_continues_degraded_on_thumbnail_failure(self, mock_deep, mock_research, mock_pkg):
        mock_deep.return_value = {}
        mock_research.return_value = {
            "topic": "Minecraft",
            "who_what": "stuff",
            "main_conflict": "betrayal",
            "emotion": "dramatic",
            "strongest_angle": "twist",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_pkg.return_value = tmpdir

            director = VideoDirector()
            with patch.object(director, "generate_thumbnail", side_effect=RuntimeError("PIL missing")):
                pkg_dir = director.produce("Minecraft", target_seconds=45.0)

            assert os.path.exists(pkg_dir)
            manifest_path = os.path.join(pkg_dir, "manifest.json")
            assert os.path.exists(manifest_path)
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            assert manifest["degraded"] is True
            assert manifest["critical_failure"] is None

    @patch("ai_video_factory.director.create_package")
    @patch("ai_video_factory.director.research_topic")
    @patch("ai_video_factory.director.deep_research")
    def test_manifest_written_on_success(self, mock_deep, mock_research, mock_pkg):
        mock_deep.return_value = {}
        mock_research.return_value = {
            "topic": "Minecraft",
            "who_what": "stuff",
            "main_conflict": "betrayal",
            "emotion": "dramatic",
            "strongest_angle": "twist",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_pkg.return_value = tmpdir

            director = VideoDirector()
            with patch.object(director, "generate_thumbnail", return_value="thumb.png"):
                pkg_dir = director.produce("Minecraft", target_seconds=45.0)

            manifest_path = os.path.join(pkg_dir, "manifest.json")
            assert os.path.exists(manifest_path)
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            assert manifest["topic"] == "Minecraft"
            assert manifest["steps"]["research"]["ok"] is True
            assert manifest["steps"]["create_package"]["ok"] is True
