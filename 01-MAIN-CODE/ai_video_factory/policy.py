"""Policy enforcer to prevent AI-slop and enforce editing rules."""
from typing import List, Dict


RULES = [
    ("no_ai_slop", "Avoid random AI-generated images and filler clips"),
    ("no_robotic_voice", "Require natural human-like voiceover or high-quality TTS"),
    ("short_hook", "Hook must be 2-5 words and appear in first 2 seconds"),
    ("subtitle_rules", "Subtitles must have 2-6 words per line and be readable"),
    ("social_package", "Include social upload metadata for Shorts/TikTok/Reels"),
]


def check_package(package_path: str) -> Dict[str, List[str]]:
    """Run a set of lightweight checks on the package and return violations.

    These are heuristic checks; human review is still required.
    """
    import os

    violations = {r[0]: [] for r in RULES}

    # 1) check for thumbnail presence
    thumbs = [f for f in os.listdir(package_path) if f.lower().startswith("thumbnail")]
    if not thumbs:
        violations["no_ai_slop"].append("No thumbnail found; create a clear subject thumbnail")

    # 2) check script length suggests hook
    script = os.path.join(package_path, "script.txt")
    if os.path.exists(script):
        txt = open(script, "r", encoding="utf-8").read().strip()
        first_line = txt.splitlines()[0] if txt.splitlines() else ""
        words = len(first_line.split())
        if not (2 <= words <= 5):
            violations["short_hook"].append(f"First line has {words} words; hook should be 2-5 words")
    else:
        violations["short_hook"].append("No script.txt found to verify hook")

    # 3) subtitles check
    srt = os.path.join(package_path, "script.srt")
    if os.path.exists(srt):
        bad = False
        for line in open(srt, "r", encoding="utf-8"):
            if line.strip() and not line.strip().isdigit() and "-->" not in line:
                # assume subtitle text line
                wc = len(line.strip().split())
                if wc > 6 or wc < 1:
                    bad = True
                    break
        if bad:
            violations["subtitle_rules"].append("Some subtitle lines exceed 6 words or are empty")
    else:
        violations["subtitle_rules"].append("No SRT found; subtitles required")

    # 4) voice check (presence only)
    vo = [f for f in os.listdir(package_path) if f.lower().startswith("voiceover") or f.lower().startswith("voice")]
    if not vo:
        violations["no_robotic_voice"].append("No voiceover file found; recommend human VO or high-quality TTS")

    # 5) social upload package metadata
    social_meta = os.path.join(package_path, "social_package.json")
    if not os.path.exists(social_meta):
        violations["social_package"].append("Missing social_package.json; publish-ready title/description/tags metadata is required")

    # remove empty lists
    violations = {k: v for k, v in violations.items() if v}
    return violations
