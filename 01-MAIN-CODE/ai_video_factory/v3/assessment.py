"""Automated editorial assessment rules separated from V3 compatibility models."""
from __future__ import annotations

from dataclasses import asdict
import re
from typing import Sequence

from .. import v3_engine as engine
from ..v3_retention import evaluate_retention_editorial_fit


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9\']+", str(text).lower())


def automated_editorial_checks(core: engine.CoreIdea, edit_type: engine.EditType, hooks: Sequence[engine.HookPack], clips: Sequence[engine.ClipBeat], retention: Sequence[engine.RetentionEvent], config: engine.V3Config) -> engine.QualityReport:
    durations = [clip.end - clip.start for clip in clips]
    overlays = [clip.text_overlay.lower() for clip in clips]
    checks = {
        "emotion_defined": bool(core.target_emotion),
        "single_edit_type_strategy": edit_type in engine.EDIT_STRATEGIES,
        "hook_under_two_seconds": bool(clips and clips[0].end <= 2.2),
        "hook_context_gap": bool(hooks and hooks[0].text and core.watch_to_end_reason),
        "every_clip_has_purpose": bool(clips) and all(bool(c.purpose) for c in clips),
        "overlay_word_limit": all(2 <= len(_tokens(c.text_overlay)) <= config.max_overlay_words for c in clips),
        "no_duplicate_overlays": len(overlays) == len(set(overlays)),
        "duration_bounds": all(config.min_clip_seconds - 0.01 <= d <= config.max_clip_seconds + 0.01 for d in durations),
        "exact_duration": bool(clips) and abs(clips[-1].end - config.target_seconds) <= 0.01,
        # Retention is judged by semantic anchors, not a fixed edit clock.
        # The independent retention report remains responsible for cadence/spacing diagnostics.
        "retention_semantic_coverage": bool(retention) and (
            any(event.time <= 0.35 for event in retention)
            and any(event.reason in {"PAYOFF_ALIGNMENT", "FINAL_IMPACT"} for event in retention)
        ),
        "has_payoff": any(c.purpose in {"Payoff","Punchline","Climax"} for c in clips),
        "has_final_impact": bool(clips and clips[-1].purpose in {"Final impact","Payoff","Reaction"}),
        "hook_payoff_continuity": bool(clips and clips[0].emotion == clips[-1].emotion),
    }
    warnings = []
    for key, message in {
        "hook_under_two_seconds":"hook exceeds preferred opening window",
        "no_duplicate_overlays":"repeated overlay text detected",
        "retention_semantic_coverage":"retention map is missing an opening or payoff anchor",
        "hook_payoff_continuity":"hook and payoff emotion differ",
    }.items():
        if not checks[key]:
            warnings.append(message)
    retention_report = evaluate_retention_editorial_fit(
        [asdict(event) for event in retention],
        duration=config.target_seconds,
        target_interval=config.retention_interval,
        clip_boundaries=[clip.start for clip in clips[1:]],
    )
    checks["retention_editorial_fit"] = retention_report.passed
    warnings.extend(f"retention: {warning}" for warning in retention_report.warnings)
    score = round(100 * sum(checks.values()) / len(checks)) if checks else 0
    blocking_checks = (
        "emotion_defined",
        "single_edit_type_strategy",
        "hook_context_gap",
        "every_clip_has_purpose",
        "overlay_word_limit",
        "no_duplicate_overlays",
        "duration_bounds",
        "exact_duration",
        "retention_semantic_coverage",
        "has_payoff",
        "has_final_impact",
        "hook_payoff_continuity",
        "retention_editorial_fit",
    )
    passed = bool(checks) and all(checks[name] for name in blocking_checks)
    return engine.QualityReport(
        passed,
        score,
        checks,
        warnings,
    )



__all__ = ["automated_editorial_checks"]
