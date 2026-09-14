"""Quality control and final human-check heuristics for AI Video Factory.

Provides a set of lightweight checks that mirror the "FINAL HUMAN CHECK"
and anti-AI-slop rules from the project guidelines. These are best-effort
heuristics intended to flag issues for a human reviewer.
"""
from typing import Dict, Any
import os
import json


def run_final_checks(pkg_dir: str) -> Dict[str, Any]:
    """Run a set of checks against the generated package directory.

    Returns a dict containing boolean results and human-readable notes.
    """
    result = {
        "package": pkg_dir,
        "checks": {},
        "ok": True,
        "notes": [],
    }

    # load research and plan if available
    research = {}
    plan = {}
    try:
        with open(os.path.join(pkg_dir, "research.json"), "r", encoding="utf-8") as f:
            research = json.load(f)
    except Exception:
        pass
    try:
        with open(os.path.join(pkg_dir, "plan.json"), "r", encoding="utf-8") as f:
            plan = json.load(f)
    except Exception:
        pass

    # 1) Hook strength: check for a short hook 2-5 words
    hook = plan.get("hook") or ""
    hook_words = len(hook.split()) if hook else 0
    hook_ok = 2 <= hook_words <= 5
    result["checks"]["hook_length"] = hook_ok
    if not hook_ok:
        result["ok"] = False
        result["notes"].append("Hook should be 2-5 words for maximum impact.")

    # 2) Script presence and length
    script = plan.get("script") or ""
    script_len = len(script.split())
    script_ok = script_len >= 10
    result["checks"]["script_present"] = script_ok
    if not script_ok:
        result["ok"] = False
        result["notes"].append("Script looks very short; consider expanding key beats.")

    # 3) Thumbnail exists
    thumbs = []
    try:
        for f in os.listdir(pkg_dir):
            if f.lower().startswith("thumbnail") and f.lower().endswith(('.png', '.jpg', '.jpeg')):
                thumbs.append(f)
    except Exception:
        pass
    thumb_ok = len(thumbs) > 0
    result["checks"]["thumbnail_exists"] = thumb_ok
    if not thumb_ok:
        result["ok"] = False
        result["notes"].append("No thumbnail found in package; create a high-contrast thumbnail.")

    # 4) Visuals: research.visuals non-empty
    visuals = research.get("visuals") or []
    visuals_ok = len(visuals) >= 1
    result["checks"]["supporting_visuals"] = visuals_ok
    if not visuals_ok:
        result["notes"].append("No supporting visuals were discovered; add screenshots or gameplay clips.")

    # 5) Anti AI-slop: check for groq_excerpt as sign of model overuse warning
    if research.get("groq_excerpt"):
        # presence is fine; but warn human to ensure phrasing feels human
        result["notes"].append("Groq enrichment detected — review VO/text to ensure it feels human and not generic.")

    # 6) Trending signal
    trending = research.get("trending", False)
    result["checks"]["trending_signal"] = trending
    if trending:
        result["notes"].append("Topic appears to have recent activity — leverage trending hooks in titles and descriptions.")

    # 7) Subtitles rule: ensure script lines would map to 2-6 words per subtitle line (heuristic)
    # We'll check average words per sentence
    sentences = [s.strip() for s in script.replace('\n', '.').split('.') if s.strip()]
    avg_words = 0
    if sentences:
        avg_words = sum(len(s.split()) for s in sentences) / len(sentences)
    subtitles_ok = 2 <= avg_words <= 6
    result["checks"]["subtitle_line_length_ok"] = subtitles_ok
    if not subtitles_ok:
        result["notes"].append("Subtitle lines may be too long or too short; target 2-6 words per line when possible.")

    # 8) Hook check: first line must be short and impactful
    hook = plan.get("hook") or ""
    hook_words = len(hook.split())
    hook_strong = 2 <= hook_words <= 5
    result["checks"]["hook_strength"] = hook_strong
    if not hook_strong:
        result["notes"].append("Hook should be 2-5 words and appear as the first line of the script.")

    # 10) Final human-sounding VO check (heuristic): if tts was used warn
    # We can't know reliably; check for existence of common TTS filenames
    tts_files = [n for n in os.listdir(pkg_dir) if n.lower().startswith("voice") or n.lower().endswith(('.mp3', '.wav'))]
    if tts_files:
        result["notes"].append("Voiceover files detected — review for natural breathing and pacing.")

    # 11) Ensure edit_plan durations roughly sum to expected short length (30-60s)
    edit_plan = plan.get("edit_plan") or []
    total_sec = 0
    try:
        for seg in edit_plan:
            if isinstance(seg, (list, tuple)) and len(seg) >= 1:
                total_sec += float(seg[0])
    except Exception:
        total_sec = 0
    plan_ok = 20 <= total_sec <= 75
    result["checks"]["edit_plan_duration_sec"] = total_sec
    result["checks"]["edit_plan_duration_ok"] = plan_ok
    if not plan_ok:
        result["notes"].append("Edit plan total duration is outside typical 30-60s range; adjust segment durations.")

    # 12) Ensure shot cadence: each shot should be between 1 and 3 seconds
    shot_lengths = []
    try:
        for seg in edit_plan:
            if isinstance(seg, (list, tuple)):
                shot_lengths.append(float(seg[0]))
            else:
                shot_lengths.append(float(seg))
    except Exception:
        pass
    avg_shot_len = (sum(shot_lengths) / len(shot_lengths)) if shot_lengths else 0
    invalid_shots = [s for s in shot_lengths if s < 1.0 or s > 3.0]
    cadence_ok = 1.0 <= avg_shot_len <= 3.0 and not invalid_shots
    result["checks"]["avg_shot_length_sec"] = avg_shot_len
    result["checks"]["cadence_ok"] = cadence_ok
    if not cadence_ok:
        result["ok"] = False
        if invalid_shots:
            result["notes"].append(
                f"Some shots are outside 1-3s: {', '.join(str(round(s,2)) for s in invalid_shots[:5])}."
            )
        else:
            result["notes"].append("Average shot length is outside 1-3s; aim for faster cuts to retain attention.")

    # 13) Story arc presence: ensure Hook, Intro, Conflict, Main event, Payoff labels exist in edit_plan
    beat_labels = [lbl for _, lbl in edit_plan]
    story_beats = {
        "Hook": any("Hook" in l for l in beat_labels),
        "Intro": any("Intro" in l for l in beat_labels),
        "Conflict": any("Conflict" in l for l in beat_labels),
        "Main event": any("Main event" in l for l in beat_labels),
        "Payoff": any("Payoff" in l for l in beat_labels),
    }
    result["checks"]["story_beat_presence"] = story_beats
    for beat, present in story_beats.items():
        result["checks"][f"has_{beat.lower().replace(' ', '_')}"] = present
    missing_beats = [beat for beat, present in story_beats.items() if not present]
    if missing_beats:
        result["ok"] = False
        result["notes"].append(
            f"Edit plan is missing key story beats: {', '.join(missing_beats)}."
        )

    style_terms = ["jump cut", "zoom", "motion blur", "impact frame", "speed ramp", "subtle shake", "cinematic transition"]
    style_present = any(
        any(term in lbl.lower() for term in style_terms)
        for lbl in beat_labels
    )
    result["checks"]["style_terms_present"] = style_present
    if not style_present:
        result["ok"] = False
        result["notes"].append(
            "Edit plan should include professional style terms like jump cut, zoom, motion blur, impact frame, subtle shake, or cinematic transition."
        )

    # 13) Shot variance: ensure each shot differs from the previous ("change something every 1-3s")
    shot_variety = 0
    if len(beat_labels) > 1:
        for i in range(1, len(beat_labels)):
            if beat_labels[i] != beat_labels[i - 1]:
                shot_variety += 1
    variety_ratio = shot_variety / max(1, len(beat_labels) - 1) if beat_labels else 0
    result["checks"]["shot_variety_ratio"] = variety_ratio
    result["checks"]["shot_variance_ok"] = variety_ratio >= 0.6
    if variety_ratio < 0.6:
        result["notes"].append(
            f"Shot variety is low ({variety_ratio:.0%}); ensure each shot differs from the previous (change something every 1-3s)."
        )

    # Wrap up
    if not result["notes"]:
        result["notes"].append("Checks passed. Perform a final human review before rendering.")

    return result
