"""Deterministic editorial, caption, originality, and asset-provenance gates.

These checks expose evidence gaps; they do not claim to determine platform
monetization eligibility or make legal conclusions.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


CONTENT_PROFILES = frozenset({
    "general",
    "gaming_auto",
    "gaming_clutch",
    "gaming_strategy",
    "gaming_funny",
    "gaming_story",
    "gaming_analysis",
})

_PROFILE_GUIDANCE = {
    "gaming_auto": (
        "Explain one specific gameplay moment and why it matters; show the setup, "
        "decision, visible consequence, and a footage-supported payoff."
    ),
    "gaming_clutch": (
        "Open on the decisive moment, briefly establish the stakes, keep the key action "
        "visible, explain the decision that changed the fight, and finish on the outcome."
    ),
    "gaming_strategy": (
        "Identify a concrete gameplay problem, show the relevant evidence, explain the "
        "decision and a plausible alternative, demonstrate the result, and end with a useful takeaway."
    ),
    "gaming_funny": (
        "Set up the situation, let the original moment play clearly, add the creator's "
        "own reaction or explanation, and end on the actual payoff without padding."
    ),
    "gaming_story": (
        "Establish the stakes, connect the observed events, explain the turning point, "
        "and conclude only with an outcome supported by the supplied footage."
    ),
    "gaming_analysis": (
        "Describe the visible action, explain the tactical or mechanical choice, connect "
        "the choice to its observable consequence, and distinguish inference from fact."
    ),
}

_GAME_NAMES = (
    "Minecraft", "Fortnite", "Valorant", "Counter-Strike 2", "CS2", "Apex Legends",
    "Call of Duty", "Warzone", "GTA V", "GTA Online", "Roblox", "Rocket League",
    "Overwatch 2", "League of Legends", "Dota 2", "Rainbow Six Siege", "Elden Ring",
    "World of Warcraft", "Modern Warfare 3", "Among Us", "Clash Royale",
)
_SRT_TIME = re.compile(r"^(\d{2,}):(\d{2}):(\d{2}),(\d{3})$")


def normalize_content_profile(value: Any) -> str:
    """Return a supported profile; unknown values never silently become a gaming preset."""
    profile = str(value or "general").strip().lower()
    return profile if profile in CONTENT_PROFILES else "general"


def content_profile_from_context(context: str | None) -> str:
    """Read the explicit dashboard marker embedded in the V3 creative context."""
    match = re.search(r"\[AIVF_CONTENT_PROFILE=([a-z_]+)\]", str(context or ""), re.IGNORECASE)
    return normalize_content_profile(match.group(1)) if match else "general"


def gaming_profile_guidance(profile: str) -> str:
    return _PROFILE_GUIDANCE.get(normalize_content_profile(profile), "")


def infer_game_name(topic: str, context: str = "") -> str:
    """Find only an explicit known game name; never guess the game from generic gameplay."""
    text = f"{topic}\n{context}"
    explicit = re.search(r"(?im)^\s*game\s*:\s*([^\n;]{1,100})", text)
    if explicit:
        value = explicit.group(1).strip()
        return value if value and value.lower() not in {"unknown", "unspecified", "n/a"} else ""
    folded = text.casefold()
    for game in _GAME_NAMES:
        if re.search(rf"(?<![a-z0-9]){re.escape(game.casefold())}(?![a-z0-9])", folded):
            return game
    return ""


def gaming_core_overrides(topic: str, profile: str) -> dict[str, str]:
    """Build cautious game-oriented story fields without inventing a gameplay outcome."""
    selected = normalize_content_profile(profile)
    subject = re.sub(r"\s+", " ", str(topic or "").strip())
    if selected == "gaming_clutch":
        return {
            "why_people_care": "Viewers can understand the pressure and the decision behind the decisive play.",
            "emotional_angle": f"The decision behind {subject}",
            "target_emotion": "dramatic",
            "watch_to_end_reason": "Show the turning point, then verify the result in the supplied gameplay.",
            "payoff": "Explain the observable outcome and what the player did to reach it.",
            "stakes": "A specific in-game decision has consequences that must be shown, not assumed.",
        }
    if selected == "gaming_strategy":
        return {
            "why_people_care": "Viewers get a concrete tactic they can understand and evaluate.",
            "emotional_angle": f"The strategy behind {subject}",
            "target_emotion": "curious",
            "watch_to_end_reason": "Follow the problem, decision, and visible result.",
            "payoff": "Give a practical takeaway supported by the footage.",
            "stakes": "A gameplay problem requires a decision whose result can be observed.",
        }
    if selected == "gaming_funny":
        return {
            "why_people_care": "The setup makes the actual reaction or reversal easier to appreciate.",
            "emotional_angle": f"How {subject} turned into chaos",
            "target_emotion": "funny",
            "watch_to_end_reason": "Let the original setup lead into the real reaction or payoff.",
            "payoff": "End on the observed funny moment or reaction without inventing dialogue.",
            "stakes": "The humour depends on the situation and the actual on-screen reaction.",
        }
    if selected == "gaming_story":
        return {
            "why_people_care": "A clear sequence of gameplay events makes the turning point understandable.",
            "emotional_angle": f"The turning point in {subject}",
            "target_emotion": "dramatic",
            "watch_to_end_reason": "Connect the setup to a turning point and a verified outcome.",
            "payoff": "Resolve the event using what the footage actually establishes.",
            "stakes": "The viewer needs to understand what changed during the gameplay moment.",
        }
    if selected == "gaming_analysis":
        return {
            "why_people_care": "The explanation connects a visible choice to its tactical consequence.",
            "emotional_angle": f"What {subject} reveals about the play",
            "target_emotion": "curious",
            "watch_to_end_reason": "See the decision, the available evidence, and the consequence.",
            "payoff": "Separate the visible result from any interpretation of why it worked.",
            "stakes": "The analysis must be supported by visible action rather than a guessed outcome.",
        }
    return {
        "why_people_care": "A specific gameplay moment is easier to follow when its decision and consequence are clear.",
        "emotional_angle": f"The gameplay moment behind {subject}",
        "target_emotion": "dramatic",
        "watch_to_end_reason": "Follow the observed action through to a footage-supported outcome.",
        "payoff": "Explain what the supplied footage actually shows at the end.",
        "stakes": "The player makes a choice with an observable in-game consequence.",
    }


def build_gaming_editorial_report(
    *,
    topic: str,
    context: str = "",
    footage_evidence: Mapping[str, Any] | None = None,
    clip_source_evidence: Sequence[Mapping[str, Any]] | None = None,
    content_profile: str | None = None,
) -> dict[str, Any]:
    """Create a reviewable, evidence-linked brief for gaming content."""
    profile = normalize_content_profile(
        content_profile or content_profile_from_context(context) or "gaming_auto"
    )
    if not profile.startswith("gaming_"):
        profile = "gaming_auto"
    game = infer_game_name(topic, context)
    scenes = [
        item for item in ((footage_evidence or {}).get("top_scenes") or [])
        if isinstance(item, Mapping)
    ]
    clips = [dict(item) for item in (clip_source_evidence or []) if isinstance(item, Mapping)]
    critical = {"hook", "payoff", "punchline", "climax", "final impact"}
    critical_clips = [
        item for item in clips
        if str(item.get("purpose") or "").strip().lower() in critical
    ]
    unsupported = [
        item for item in critical_clips
        if str(item.get("status") or "").strip().lower() != "supported"
    ]
    return {
        "schema_version": "1.0.0",
        "content_profile": profile,
        "game": game or None,
        "event_brief": str(topic or "").strip(),
        "angle_guidance": gaming_profile_guidance(profile),
        "creator_contribution_required": True,
        "commentary_contract": [
            "Use the player's actual actions or decisions as the basis for claims.",
            "Explain why a visible decision matters instead of narrating events only.",
            "Do not invent kills, wins, tactics, reactions, quotes, or outcomes.",
            "Distinguish observed footage from interpretation and uncertain context.",
            "Keep a clear setup, decision/turning point, and footage-supported payoff.",
        ],
        "source_evidence": {
            "scene_count": len(scenes),
            "critical_clip_count": len(critical_clips),
            "critical_clips": [
                {
                    "clip_index": item.get("clip_index"),
                    "purpose": item.get("purpose"),
                    "status": item.get("status"),
                    "source_scene": item.get("source_scene"),
                    "source_start": item.get("source_start"),
                    "source_end": item.get("source_end"),
                    "reason": item.get("reason"),
                }
                for item in critical_clips
            ],
            "unsupported_critical_clip_indices": [
                item.get("clip_index") for item in unsupported
            ],
        },
        "requires_human_review": not bool(game) or not bool(scenes) or bool(unsupported),
        "publish_blocked": bool(unsupported),
        "warnings": [
            warning for condition, warning in (
                (not bool(game), "Game not identified explicitly; reviewer must confirm it."),
                (not bool(scenes), "No analyzed source-scene evidence is available."),
                (bool(unsupported), "One or more critical beats lack supported gameplay evidence."),
            ) if condition
        ],
        "human_approval_status": "pending",
    }


def _srt_seconds(value: str) -> float | None:
    match = _SRT_TIME.fullmatch(value.strip())
    if not match:
        return None
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def validate_srt_captions(
    path: str | Path,
    *,
    media_duration_seconds: float,
    max_line_characters: int = 42,
    max_lines: int = 2,
    max_characters_per_second: float = 24.0,
) -> dict[str, Any]:
    """Validate SRT syntax, order, media bounds, line overflow, and reading speed."""
    target = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    cues: list[dict[str, Any]] = []
    if target.is_symlink() or not target.is_file():
        return {"passed": False, "status": "invalid", "errors": ["caption file is missing or unsafe"], "warnings": [], "cue_count": 0}
    try:
        raw = target.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return {"passed": False, "status": "invalid", "errors": [f"caption file cannot be read: {type(exc).__name__}"], "warnings": [], "cue_count": 0}

    blocks = [block for block in re.split(r"\r?\n\s*\r?\n", raw.strip()) if block.strip()]
    if not blocks:
        errors.append("caption file contains no subtitle cues")
    previous_start = -1.0
    previous_end = -1.0
    try:
        duration = float(media_duration_seconds)
    except (TypeError, ValueError, OverflowError):
        duration = 0.0
    if duration <= 0:
        errors.append("caption validation requires a valid positive media duration")

    for block_index, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            errors.append(f"caption block {block_index} has an invalid index or missing timing")
            continue
        timing = re.fullmatch(r"\s*(\d{2,}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2,}:\d{2}:\d{2},\d{3})(?:\s+.*)?", lines[1])
        if not timing:
            errors.append(f"caption block {block_index} has invalid SRT timing syntax")
            continue
        start = _srt_seconds(timing.group(1))
        end = _srt_seconds(timing.group(2))
        text_lines = [line.strip() for line in lines[2:] if line.strip()]
        caption_text = " ".join(text_lines)
        if start is None or end is None or end <= start:
            errors.append(f"caption block {block_index} has invalid or non-positive timing")
            continue
        if start < previous_start or start < previous_end - 0.05:
            errors.append(f"caption block {block_index} overlaps or is out of chronological order")
        if start < 0 or end > duration + 0.20:
            errors.append(
                f"caption block {block_index} is outside media bounds ({start:.3f}–{end:.3f}s; "
                f"media {duration:.3f}s)"
            )
        if not caption_text:
            errors.append(f"caption block {block_index} has no text")
        if len(text_lines) > max_lines:
            errors.append(f"caption block {block_index} exceeds {max_lines} lines")
        if any(len(line) > max_line_characters for line in text_lines):
            errors.append(
                f"caption block {block_index} exceeds {max_line_characters} characters per line"
            )
        cue_duration = end - start
        chars_per_second = len(caption_text) / cue_duration if cue_duration > 0 else float("inf")
        if cue_duration < 0.25:
            warnings.append(f"caption block {block_index} is displayed for less than 0.25 seconds")
        if chars_per_second > max_characters_per_second:
            errors.append(
                f"caption block {block_index} reading speed {chars_per_second:.1f} characters/s "
                f"exceeds {max_characters_per_second:.1f}"
            )
        elif chars_per_second > 20.0:
            warnings.append(f"caption block {block_index} may be difficult to read at {chars_per_second:.1f} characters/s")
        cues.append({"index": int(lines[0].strip()), "start_seconds": start, "end_seconds": end, "characters": len(caption_text), "characters_per_second": round(chars_per_second, 2)})
        previous_start, previous_end = start, end

    passed = not errors and bool(cues)
    return {
        "passed": passed,
        "status": "passed" if passed else "failed",
        "errors": errors,
        "warnings": warnings,
        "cue_count": len(cues),
        "max_line_characters": max_line_characters,
        "max_lines": max_lines,
        "max_characters_per_second": max_characters_per_second,
        "cues": cues,
        "safe_margins": {"status": "manual_review_required", "automated_claim": False},
    }


def _normalized_script(script: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9']+", str(script or "").casefold())
    return tokens


def compare_script_originality(
    current_script: str,
    previous_scripts: Sequence[Mapping[str, Any]],
    *,
    similarity_threshold: float = 0.82,
) -> dict[str, Any]:
    """Flag near-copies; shared intros/styles alone do not constitute a block."""
    current = _normalized_script(current_script)
    if len(current) < 30:
        return {
            "status": "insufficient_data",
            "publish_blocked": False,
            "reason": "At least 30 script tokens are needed for a useful comparison.",
            "comparisons": [],
        }
    comparisons: list[dict[str, Any]] = []
    for item in previous_scripts:
        if not isinstance(item, Mapping):
            continue
        previous = _normalized_script(str(item.get("script") or ""))
        if len(previous) < 30:
            continue
        # Sequence similarity preserves the order of the story and is less likely
        # to flag videos merely sharing a short intro or recurring series format.
        ratio = SequenceMatcher(a=current, b=previous, autojunk=False).ratio()
        comparisons.append({
            "package": str(item.get("package") or ""),
            "topic": str(item.get("topic") or ""),
            "similarity": round(ratio, 4),
            "token_count": len(previous),
            "review_required": ratio >= similarity_threshold,
        })
    comparisons.sort(key=lambda item: item["similarity"], reverse=True)
    matched = [item for item in comparisons if item["review_required"]]
    return {
        "status": "review_required" if matched else ("compared" if comparisons else "insufficient_data"),
        "publish_blocked": bool(matched),
        "reason": (
            "A recent script is unusually similar; verify distinct commentary and creator contribution."
            if matched else "No near-copy was found in the available recent scripts."
        ),
        "similarity_threshold": similarity_threshold,
        "comparisons": comparisons[:20],
        "originality_review": {"status": "pending", "requires_distinct_contribution": bool(matched)},
    }


def compare_recent_package_scripts(
    package_dir: str | Path,
    current_script: str,
    *,
    max_recent: int = 20,
    similarity_threshold: float = 0.82,
) -> dict[str, Any]:
    """Compare with recent sibling output packages without reading arbitrary paths."""
    current_path = Path(package_dir)
    try:
        candidates = [
            path for path in current_path.parent.iterdir()
            if path.is_dir() and not path.is_symlink() and path.resolve() != current_path.resolve()
        ]
    except OSError:
        candidates = []
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    previous: list[dict[str, Any]] = []
    for package in candidates[:max(1, min(100, int(max_recent)))]:
        script_path = package / "script.txt"
        if script_path.is_symlink() or not script_path.is_file():
            continue
        try:
            old_script = script_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        topic = package.name
        for meta_path in (package / "upload" / "metadata.json", package / "metadata.json"):
            if meta_path.is_symlink() or not meta_path.is_file():
                continue
            try:
                import json
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(meta, Mapping):
                    topic = str(meta.get("topic") or meta.get("selected_title") or topic)
                    break
            except (OSError, ValueError, TypeError):
                continue
        previous.append({"package": package.name, "topic": topic, "script": old_script})
    report = compare_script_originality(
        current_script,
        previous,
        similarity_threshold=similarity_threshold,
    )
    report["comparison_count"] = len(report.get("comparisons") or [])
    report["looked_at_packages"] = [item["package"] for item in previous]
    return report


def _yes_no_unknown(value: Any) -> str:
    raw = str(value if value is not None else "").strip().lower()
    if isinstance(value, bool):
        return "yes" if value else "no"
    if raw in {"yes", "true", "1", "required"}:
        return "yes"
    if raw in {"no", "false", "0", "not_required"}:
        return "no"
    return "unknown"


def asset_provenance_report(
    sources: Sequence[Mapping[str, Any]],
    *,
    human_review: Mapping[str, Any] | None,
    required: bool,
) -> dict[str, Any]:
    """Require explicit origin, commercial-use scope, and attribution duty per source."""
    if not sources:
        return {
            "status": "not_declared",
            "publish_blocked": bool(required),
            "errors": ["no asset provenance records are available"] if required else [],
            "assets": [],
            "human_approval_status": str((human_review or {}).get("status") or "review_required"),
        }
    supported_origins = {
        "original_gameplay", "original_voice", "ai_generated", "licensed",
        "third_party", "user_provided", "user_upload", "unknown",
    }
    assets: list[dict[str, Any]] = []
    all_errors: list[str] = []
    for index, source in enumerate(sources, start=1):
        item = dict(source)
        asset_id = str(item.get("asset_id") or item.get("title") or item.get("url") or f"asset-{index}")
        origin = str(item.get("asset_origin") or "unknown").strip().lower()
        commercial = _yes_no_unknown(item.get("permitted_commercial_use"))
        attribution_required = _yes_no_unknown(item.get("attribution_required"))
        attribution = str(item.get("attribution") or "").strip()
        errors: list[str] = []
        if required:
            if origin not in supported_origins or origin == "unknown":
                errors.append("asset origin/type is not explicitly declared")
            if commercial == "unknown":
                errors.append("permitted commercial use is not explicitly declared")
            elif commercial == "no":
                errors.append("asset is not permitted for commercial use")
            if attribution_required == "unknown":
                errors.append("attribution requirement is not explicitly declared")
            elif attribution_required == "yes" and not attribution:
                errors.append("required attribution text is missing")
            if origin == "licensed" and not (
                str(item.get("license") or "").strip()
                and str(item.get("license_url") or "").strip()
            ):
                errors.append("licensed asset is missing a license name or license URL")
            if origin in {"third_party", "licensed"} and not (
                str(item.get("url") or "").strip()
                and (str(item.get("evidence_url") or "").strip() or str(item.get("license_url") or "").strip())
            ):
                errors.append("third-party asset is missing its source URL or permission/license evidence")
        assets.append({
            "asset_id": asset_id,
            "origin": origin,
            "source_url": str(item.get("url") or item.get("source_url") or ""),
            "creator_or_supplier": str(item.get("creator") or ""),
            "license": str(item.get("license") or ""),
            "license_url": str(item.get("license_url") or ""),
            "permission_evidence_url": str(item.get("evidence_url") or ""),
            "permitted_commercial_use": commercial,
            "attribution_required": attribution_required,
            "attribution": attribution,
            "errors": errors,
        })
        all_errors.extend(f"{asset_id}: {error}" for error in errors)
    approval_status = str((human_review or {}).get("status") or "review_required")
    if required and approval_status != "approved":
        all_errors.append("human approval status is not approved")
    return {
        "status": "cleared" if not all_errors else "review_required",
        "publish_blocked": bool(all_errors),
        "errors": all_errors,
        "assets": assets,
        "human_approval_status": approval_status,
        "commercial_use_is_explicit": True,
        "legal_conclusion": False,
    }


__all__ = [
    "CONTENT_PROFILES",
    "asset_provenance_report",
    "build_gaming_editorial_report",
    "compare_recent_package_scripts",
    "compare_script_originality",
    "content_profile_from_context",
    "gaming_core_overrides",
    "gaming_profile_guidance",
    "infer_game_name",
    "normalize_content_profile",
    "validate_srt_captions",
]
