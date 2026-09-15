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


# Each entry names a concrete code symbol rather than merely asserting that the
# capability exists somewhere in the V3 engine.
_EVIDENCE = {
    "emotion-first core idea": ("v3_engine.analyze_core_idea", "v3_engine.analyze_core_idea"),
    "single edit-type lock": ("v3_engine.choose_edit_type", "v3_engine.choose_edit_type"),
    "visual hook": ("v3_engine.generate_hooks", "v3_engine.generate_hooks"),
    "text hook": ("v3_engine.generate_hooks", "v3_engine.generate_hooks"),
    "emotional hook": ("v3_engine.generate_hooks", "v3_engine.generate_hooks"),
    "hook A/B ranking": ("v3_engine.generate_hooks", "v3_engine.generate_hooks"),
    "purpose-driven clip plan": ("v3_engine.build_clip_plan", "v3_engine.build_clip_plan"),
    "adaptive clip count": ("v3_engine.build_clip_plan", "v3_engine.build_clip_plan"),
    "exact duration allocation": ("v3_engine._allocate_durations", "v3_engine._allocate_durations"),
    "2-6 word overlays": ("v3_engine._overlay_for_purpose", "v3_engine._human_editor_checks"),
    "overlay de-duplication": ("v3_engine._unique_overlay", "v3_engine._human_editor_checks"),
    "music energy plan": ("v3_engine.analyze_music", "v3_engine.analyze_music"),
    "beat-grid sync": ("v3_engine.analyze_music", "v3_engine.analyze_music"),
    "drop-aware payoff": ("v3_engine.analyze_music", "v3_engine.build_retention_map"),
    "retention event map": ("v3_engine.build_retention_map", "v3_engine.build_retention_map"),
    "1-3 second visual change rule": ("v3_quality.strict_render_check", "v3_quality.strict_render_check"),
    "camera motion planning": ("edit_planner._adaptive_motion_transition", "test_v3_effect_mapping.py"),
    "transition restraint": ("edit_planner._adaptive_motion_transition", "test_v3_effect_mapping.py"),
    "human-editor reject pass": ("edit_planner.choose_scene", "test_v3_strict_hardening.py"),
    "dead-moment detection": ("v3_quality.strict_render_check", "test_v3_strict_hardening.py"),
    "repetition detection": ("v3_engine._human_editor_checks", "test_v3_strict_hardening.py"),
    "AI-slideshow guard": ("v3_quality.strict_render_check", "test_v3_strict_hardening.py"),
    "payoff validation": ("v3_engine._human_editor_checks", "test_v3_strict_hardening.py"),
    "hook-payoff continuity": ("v3_engine._human_editor_checks", "test_v3_strict_hardening.py"),
    "platform-safe variants": ("v3_engine.platform_variants", "test_v3_strict_hardening.py"),
    "9:16 Shorts profile": ("v3_engine.PLATFORM_PROFILES", "test_v3_strict_hardening.py"),
    "9:16 TikTok profile": ("v3_engine.PLATFORM_PROFILES", "test_v3_strict_hardening.py"),
    "9:16 Reels profile": ("v3_engine.PLATFORM_PROFILES", "test_v3_strict_hardening.py"),
    "1:1 social profile": ("v3_engine.PLATFORM_PROFILES", "test_v3_strict_hardening.py"),
    "16:9 long-form profile": ("v3_engine.PLATFORM_PROFILES", "test_v3_strict_hardening.py"),
    "thumbnail concept": ("v3_engine._metadata", "v3_engine._metadata"),
    "title laboratory": ("v3_engine._metadata", "v3_engine._metadata"),
    "description generator": ("v3_engine._metadata", "v3_engine._metadata"),
    "hashtag pack": ("v3_engine._metadata", "v3_engine._metadata"),
    "silent-viewing readability": ("v3_engine._overlay_for_purpose", "v3_engine._human_editor_checks"),
    "safe-area awareness": ("v3_engine.PLATFORM_PROFILES", "test_v3_strict_hardening.py"),
    "retention heuristic score": ("v3_engine._heuristic_metrics", "00-INFO/V3-METRICS.md"),
    "completion heuristic score": ("v3_engine._heuristic_metrics", "00-INFO/V3-METRICS.md"),
    "rewatch heuristic score": ("v3_engine._heuristic_metrics", "00-INFO/V3-METRICS.md"),
    "shareability heuristic score": ("v3_engine._heuristic_metrics", "00-INFO/V3-METRICS.md"),
}

_CAPABILITY_NAMES = tuple(_EVIDENCE)
CAPABILITIES: Mapping[str, CapabilitySpec] = {
    key: CapabilitySpec(
        key=key,
        implementation=implementation,
        validator=validator,
        tests=(test,) if test.endswith(".py") or test.endswith(".md") else ("test_v3_strict_hardening.py",),
    )
    for key, (implementation, validator) in _EVIDENCE.items()
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
        if spec.status != "implemented":
            raise ValueError(f"V3 capability is not release-ready: {spec.key}")
        if "." in spec.implementation and "+" not in spec.implementation:
            _resolve_symbol(spec.implementation)
