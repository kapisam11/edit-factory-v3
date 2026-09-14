"""Review system that scores a package against human-editing criteria.

Provides heuristic scoring for Story, Emotion, Retention, Visual Quality,
and Editing Style. Optionally calls into a model via `model_adapter.call_model`
to get rewrite suggestions when an API key is provided.
"""
import os
import json
from typing import Dict, Any, Optional
from . import story
from . import model_adapter


DEFAULT_CRITERIA = {
    "story_score_min": 0.7,
    "emotion_score_min": 0.6,
    "retention_score_min": 0.65,
}


def _heuristic_story_score(plan: Dict[str, Any]) -> float:
    # base score on presence of Hook/Climax/Payoff and script length
    score = 0.0
    hook = bool(plan.get("hook"))
    script = plan.get("script", "")
    edit_plan = plan.get("edit_plan", [])
    has_climax = any("Climax" in (lbl or "") for _, lbl in edit_plan)
    has_payoff = any("Payoff" in (lbl or "") for _, lbl in edit_plan)
    score += 0.4 if hook else 0.0
    score += 0.3 if has_climax else 0.0
    score += 0.2 if has_payoff else 0.0
    score += min(0.1, len(script.split()) / 200.0)
    return min(1.0, score)


def _heuristic_emotion_score(plan: Dict[str, Any]) -> float:
    script = plan.get("script", "")
    emotive_words = [w for w in script.lower().split() if w.endswith("ing") or w in ("sad", "angry", "love", "fear", "surprise", "stunned")]
    base = min(1.0, len(emotive_words) / 4.0)
    return base * 0.9 + 0.1


def _heuristic_retention_score(plan: Dict[str, Any], qc: Dict[str, Any]) -> float:
    # use avg shot length and presence of hook/climax
    avg_shot = qc.get("checks", {}).get("avg_shot_length_sec", 2.0)
    shot_score = 1.0 if 1.0 <= avg_shot <= 2.5 else max(0.0, 1.0 - abs(avg_shot - 2.0) / 4.0)
    hook = bool(plan.get("hook"))
    hook_score = 1.0 if hook else 0.0
    return 0.7 * shot_score + 0.3 * hook_score


def run_review(pkg_dir: str, use_model: bool = False, model_key: Optional[str] = None, criteria: Dict[str, float] = None) -> Dict[str, Any]:
    """Run review and return a report with scores and suggestions.

    If `use_model` is True and `model_key` provided, the adapter will be used
    to request rewrite suggestions.
    """
    criteria = criteria or DEFAULT_CRITERIA
    report: Dict[str, Any] = {"pkg": pkg_dir, "scores": {}, "ok": True, "notes": [], "suggestions": []}

    # load plan and qc
    plan = {}
    try:
        with open(os.path.join(pkg_dir, "plan.json"), "r", encoding="utf-8") as f:
            plan = json.load(f)
    except Exception:
        pass

    qc = {}
    try:
        from .quality_control import run_final_checks
        qc = run_final_checks(pkg_dir)
    except Exception:
        qc = {}

    story_score = _heuristic_story_score(plan)
    emotion_score = _heuristic_emotion_score(plan)
    retention_score = _heuristic_retention_score(plan, qc)

    report["scores"]["story"] = story_score
    report["scores"]["emotion"] = emotion_score
    report["scores"]["retention"] = retention_score

    if story_score < criteria["story_score_min"]:
        report["ok"] = False
        report["notes"].append("Story score below threshold; strengthen hook/climax/payoff and expand script.")
        report["suggestions"].append("Call story.enforce_story_arc and rewrite script for emotion.")

    if emotion_score < criteria["emotion_score_min"]:
        report["ok"] = False
        report["notes"].append("Emotional intensity appears low; add sensory details and stronger verbs.")
        report["suggestions"].append("Use model-driven rewrite or `story.rewrite_script_emotional`.")

    if retention_score < criteria["retention_score_min"]:
        report["ok"] = False
        report["notes"].append("Retention heuristics suggest pacing or hook issues.")
        report["suggestions"].append("Shorten average shot length or add earlier payoff cues.")

    # Optional model-driven suggestion
    if use_model:
        prompt = f"Review this short plan and script for story, emotion, retention. Plan: {json.dumps(plan)}\nProvide concise suggestions and a rewrite of the script focusing on emotional verbs and sensory details." 
        model_out = model_adapter.call_model(prompt, api_key=model_key)
        if model_out:
            report["suggestions"].append(model_out)

    return report


def apply_auto_fixes(pkg_dir: str) -> bool:
    """Apply conservative auto-fixes: enforce story arc and rewrite script.

    Returns True if changes were written.
    """
    plan_path = os.path.join(pkg_dir, "plan.json")
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except Exception:
        return False

    try:
        fixed = story.enforce_story_arc(plan)
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(fixed, f, indent=2)
        return True
    except Exception:
        return False


def insert_early_payoff(pkg_dir: str, percent: float = 0.35) -> bool:
    """Insert a short Payoff beat earlier in the edit_plan at the given percent of total duration.

    Returns True if modified.
    """
    plan_path = os.path.join(pkg_dir, "plan.json")
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except Exception:
        return False

    edit_plan = plan.get("edit_plan", [])
    if not edit_plan:
        return False

    total = sum([float(s[0]) for s in edit_plan])
    target = total * percent
    acc = 0.0
    for i, s in enumerate(edit_plan):
        acc += float(s[0])
        if acc >= target:
            # insert a short payoff before this index
            payoff_len = max(1.5, total * 0.08)
            edit_plan.insert(i, (round(payoff_len, 2), "Early Payoff - emotional hook"))
            plan["edit_plan"] = edit_plan
            try:
                with open(plan_path, "w", encoding="utf-8") as f:
                    json.dump(plan, f, indent=2)
                return True
            except Exception:
                return False
    return False
