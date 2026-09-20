"""Machine-readable V3 capability registry with concrete implementation evidence."""
from __future__ import annotations

from dataclasses import dataclass
import importlib
from typing import Mapping, Tuple


@dataclass(frozen=True)
class CapabilitySpec:
    key: str
    implementation: str
    validator: str
    tests: Tuple[str, ...]
    status: str = "implemented"
    evidence_level: str = "deterministic"
    display_name: str = ""
    truth_note: str = ""


_TEST_STRICT = "test_v3_strict_hardening.py"
_TEST_EFFECTS = "test_v3_effect_mapping.py"

_EVIDENCE = {
    "emotion-first core idea": ("v3_engine.analyze_core_idea", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "single edit-type lock": ("v3_engine.choose_edit_type", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "visual hook": ("v3_engine.generate_hooks", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "text hook": ("v3_engine.generate_hooks", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "emotional hook": ("v3_engine.generate_hooks", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "hook A/B ranking": ("v3_engine.generate_hooks", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "purpose-driven clip plan": ("v3_engine.build_clip_plan", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "adaptive clip count": ("v3_engine.build_clip_plan", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "exact duration allocation": ("v3_engine._allocate_durations", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "2-6 word overlays": ("v3_engine._overlay_for_purpose", "v3_engine._human_editor_checks", (_TEST_STRICT,)),
    "overlay de-duplication": ("v3_engine._unique_overlay", "v3_engine._human_editor_checks", (_TEST_STRICT,)),
    "music energy plan": ("v3_engine.analyze_music", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "beat-grid sync": ("v3_engine.analyze_music", "v3_engine.validate_blueprint", (_TEST_STRICT,)),
    "drop-aware payoff": ("v3_engine.analyze_music", "v3_engine.build_retention_map", (_TEST_STRICT,)),
    "retention event map": ("v3_engine.build_retention_map", "v3_quality.strict_render_check", (_TEST_STRICT,)),
    "1-3 second visual change rule": ("v3_quality.strict_render_check", "v3_quality.strict_render_check", (_TEST_STRICT,)),
    "camera motion planning": ("edit_planner._adaptive_motion_transition", "effects_engine.build_cinematic_filter", (_TEST_EFFECTS,)),
    "transition restraint": ("edit_planner._adaptive_motion_transition", "effects_engine.build_cinematic_filter", (_TEST_EFFECTS,)),
    "human-editor reject pass": ("edit_planner.choose_scene", "edit_planner.choose_scene", (_TEST_STRICT,)),
    "dead-moment detection": ("v3_quality.strict_render_check", "v3_quality.strict_render_check", (_TEST_STRICT,)),
    "repetition detection": ("v3_engine._human_editor_checks", "v3_engine._human_editor_checks", (_TEST_STRICT,)),
    "AI-slideshow guard": ("v3_quality.strict_render_check", "v3_quality.strict_render_check", (_TEST_STRICT,)),
    "payoff validation": ("v3_engine._human_editor_checks", "v3_engine._human_editor_checks", (_TEST_STRICT,)),
    "hook-payoff continuity": ("v3_engine._human_editor_checks", "v3_engine._human_editor_checks", (_TEST_STRICT,)),
    "platform-safe variants": ("v3_engine.platform_variants", "v3_engine.platform_variants", (_TEST_STRICT,)),
    "9:16 Shorts profile": ("v3_engine.PLATFORM_PROFILES", "v3_engine.platform_variants", (_TEST_STRICT,)),
    "9:16 TikTok profile": ("v3_engine.PLATFORM_PROFILES", "v3_engine.platform_variants", (_TEST_STRICT,)),
    "9:16 Reels profile": ("v3_engine.PLATFORM_PROFILES", "v3_engine.platform_variants", (_TEST_STRICT,)),
    "1:1 social profile": ("v3_engine.PLATFORM_PROFILES", "v3_engine.platform_variants", (_TEST_STRICT,)),
    "16:9 long-form profile": ("v3_engine.PLATFORM_PROFILES", "v3_engine.platform_variants", (_TEST_STRICT,)),
    "thumbnail concept": ("v3_engine._metadata", "v3_engine._metadata", (_TEST_STRICT,)),
    "title laboratory": ("v3_engine._metadata", "v3_engine._metadata", (_TEST_STRICT,)),
    "description generator": ("v3_engine._metadata", "v3_engine._metadata", (_TEST_STRICT,)),
    "hashtag pack": ("v3_engine._metadata", "v3_engine._metadata", (_TEST_STRICT,)),
    "silent-viewing readability": ("v3_engine._overlay_for_purpose", "v3_engine._human_editor_checks", (_TEST_STRICT,)),
    "safe-area awareness": ("v3_engine.PLATFORM_PROFILES", "v3_engine.platform_variants", (_TEST_STRICT,)),
    "retention heuristic score": ("v3_engine._heuristic_metrics", "v3_engine._heuristic_metrics", (_TEST_STRICT,)),
    "completion heuristic score": ("v3_engine._heuristic_metrics", "v3_engine._heuristic_metrics", (_TEST_STRICT,)),
    "rewatch heuristic score": ("v3_engine._heuristic_metrics", "v3_engine._heuristic_metrics", (_TEST_STRICT,)),
    "shareability heuristic score": ("v3_engine._heuristic_metrics", "v3_engine._heuristic_metrics", (_TEST_STRICT,)),
}

_HEURISTIC_KEYS = {
    "human-editor reject pass",
    "dead-moment detection",
    "AI-slideshow guard",
    "payoff validation",
    "hook-payoff continuity",
    "retention heuristic score",
    "completion heuristic score",
    "rewatch heuristic score",
    "shareability heuristic score",
}
_HYBRID_SEMANTIC_KEYS = {
    "emotion-first core idea",
    "visual hook",
    "text hook",
    "emotional hook",
    "hook A/B ranking",
    "purpose-driven clip plan",
    "adaptive clip count",
    "repetition detection",
    "silent-viewing readability",
}
_DISPLAY_NAMES = {
    "human-editor reject pass": "editorial reject-rule pass",
    "dead-moment detection": "dead-moment heuristic",
    "AI-slideshow guard": "slideshow-cadence heuristic",
    "payoff validation": "payoff-structure check",
    "hook-payoff continuity": "hook-payoff continuity check",
}


def _spec(key: str, implementation: str, validator: str, tests: Tuple[str, ...]) -> CapabilitySpec:
    if key in _HEURISTIC_KEYS:
        level, status = "heuristic", "heuristic"
        note = "Deterministic/editorial heuristic; not equivalent to human review or a platform prediction."
    elif key in _HYBRID_SEMANTIC_KEYS:
        level, status = "hybrid_semantic", "hybrid"
        note = "Uses semantic/lexical evidence where available with deterministic fallback."
    else:
        level, status = "deterministic_contract", "implemented"
        note = "Deterministic implementation with repository-level validation evidence."
    return CapabilitySpec(
        key=key,
        implementation=implementation,
        validator=validator,
        tests=tests,
        status=status,
        evidence_level=level,
        display_name=_DISPLAY_NAMES.get(key, key),
        truth_note=note,
    )


CAPABILITIES: Mapping[str, CapabilitySpec] = {
    key: _spec(key, implementation, validator, tests)
    for key, (implementation, validator, tests) in _EVIDENCE.items()
}


def _resolve_symbol(path: str):
    module_name, symbol_path = path.split(".", 1)
    module = importlib.import_module(f"ai_video_factory.{module_name}")
    value = module
    for part in symbol_path.split("."):
        value = getattr(value, part)
    return value


def validate_capabilities() -> None:
    if len(CAPABILITIES) != 40:
        raise ValueError(f"V3 capability registry must contain 40 capabilities, found {len(CAPABILITIES)}")
    from .v3_engine import V3_CAPABILITIES

    if set(CAPABILITIES) != set(V3_CAPABILITIES):
        missing = sorted(set(V3_CAPABILITIES) - set(CAPABILITIES))
        extra = sorted(set(CAPABILITIES) - set(V3_CAPABILITIES))
        raise ValueError(f"V3 capability registry drift: missing={missing}, extra={extra}")

    for spec in CAPABILITIES.values():
        if not spec.implementation or not spec.validator or not spec.tests:
            raise ValueError(f"V3 capability missing evidence: {spec.key}")
        if spec.status not in {"implemented", "hybrid", "heuristic"}:
            raise ValueError(f"V3 capability has unknown evidence status: {spec.key}")
        if spec.status == "heuristic" and spec.evidence_level != "heuristic":
            raise ValueError(f"heuristic capability has incorrect evidence level: {spec.key}")
        if not spec.display_name or not spec.truth_note:
            raise ValueError(f"V3 capability is missing a truthfulness label: {spec.key}")
        _resolve_symbol(spec.implementation)
        _resolve_symbol(spec.validator)
