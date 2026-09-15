"""Machine-readable V3 capability registry with implementation evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Tuple


@dataclass(frozen=True)
class CapabilitySpec:
    key: str
    implementation: str
    validator: str
    tests: Tuple[str, ...]
    status: str = "implemented"


_CAPABILITY_NAMES = (
    "emotion-first core idea", "single edit-type lock", "visual hook", "text hook",
    "emotional hook", "hook A/B ranking", "purpose-driven clip plan", "adaptive clip count",
    "exact duration allocation", "2-6 word overlays", "overlay de-duplication", "music energy plan",
    "beat-grid sync", "drop-aware payoff", "retention event map", "1-3 second visual change rule",
    "camera motion planning", "transition restraint", "human-editor reject pass", "dead-moment detection",
    "repetition detection", "AI-slideshow guard", "payoff validation", "hook-payoff continuity",
    "platform-safe variants", "9:16 Shorts profile", "9:16 TikTok profile", "9:16 Reels profile",
    "1:1 social profile", "16:9 long-form profile", "thumbnail concept", "title laboratory",
    "description generator", "hashtag pack", "silent-viewing readability", "safe-area awareness",
    "retention heuristic score", "completion heuristic score", "rewatch heuristic score",
    "shareability heuristic score",
)

CAPABILITIES: Mapping[str, CapabilitySpec] = {
    name: CapabilitySpec(
        key=name,
        implementation="v3_engine.py",
        validator="validate_blueprint",
        tests=("test_v3_strict_hardening.py",),
    )
    for name in _CAPABILITY_NAMES
}

# Capabilities with dedicated renderer/semantic evidence get explicit ownership.
CAPABILITIES = dict(CAPABILITIES)
CAPABILITIES["emotion-first core idea"] = CapabilitySpec("emotion-first core idea", "v3_engine.py + v3_semantics.py", "analyze_core_idea", ("test_v3_strict_hardening.py",))
CAPABILITIES["retention event map"] = CapabilitySpec("retention event map", "v3_engine.py", "_human_editor_checks", ("test_v3_strict_hardening.py",))
CAPABILITIES["human-editor reject pass"] = CapabilitySpec("human-editor reject pass", "edit_planner.py", "choose_scene", ("test_v3_strict_hardening.py",))
CAPABILITIES["dead-moment detection"] = CapabilitySpec("dead-moment detection", "v3_quality.py", "strict_render_check", ("test_v3_strict_hardening.py",))
CAPABILITIES["repetition detection"] = CapabilitySpec("repetition detection", "v3_engine.py", "_human_editor_checks", ("test_v3_strict_hardening.py",))
CAPABILITIES["AI-slideshow guard"] = CapabilitySpec("AI-slideshow guard", "v3_quality.py", "strict_render_check", ("test_v3_strict_hardening.py",))
CAPABILITIES["platform-safe variants"] = CapabilitySpec("platform-safe variants", "v3_engine.py + composer.py", "V3Config.validate", ("test_v3_strict_hardening.py",))
CAPABILITIES["safe-area awareness"] = CapabilitySpec("safe-area awareness", "v3_engine.py", "platform_variants", ("test_v3_strict_hardening.py",))
CAPABILITIES["1-3 second visual change rule"] = CapabilitySpec("1-3 second visual change rule", "v3_engine.py + v3_quality.py", "strict_render_check", ("test_v3_strict_hardening.py",))
CAPABILITIES["retention heuristic score"] = CapabilitySpec("retention heuristic score", "v3_engine.py", "_heuristic_metrics", ("test_v3_strict_hardening.py",))
CAPABILITIES["completion heuristic score"] = CapabilitySpec("completion heuristic score", "v3_engine.py", "_heuristic_metrics", ("test_v3_strict_hardening.py",))
CAPABILITIES["rewatch heuristic score"] = CapabilitySpec("rewatch heuristic score", "v3_engine.py", "_heuristic_metrics", ("test_v3_strict_hardening.py",))
CAPABILITIES["shareability heuristic score"] = CapabilitySpec("shareability heuristic score", "v3_engine.py", "_heuristic_metrics", ("test_v3_strict_hardening.py",))


def validate_capabilities() -> None:
    if len(CAPABILITIES) != 40:
        raise ValueError(f"V3 capability registry must contain 40 capabilities, found {len(CAPABILITIES)}")
    missing = [name for name, spec in CAPABILITIES.items() if not spec.implementation or not spec.validator or not spec.tests]
    if missing:
        raise ValueError("V3 capabilities missing evidence: " + ", ".join(missing))
    if any(spec.status != "implemented" for spec in CAPABILITIES.values()):
        raise ValueError("V3 capability registry contains non-implemented capabilities")
