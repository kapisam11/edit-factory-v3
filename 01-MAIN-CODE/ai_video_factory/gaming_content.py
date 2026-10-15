"""Gaming-commentary editorial requirements and evidence-backed release gating.

This module deliberately does not infer that gameplay events occurred or that a
creator's angle is original. It records supplied evidence and asks for review
when the brief or source-scene support is incomplete.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

GENERAL_PROFILE = "general"
GAMING_COMMENTARY_PROFILE = "gaming_commentary"
SUPPORTED_CONTENT_PROFILES = frozenset({GENERAL_PROFILE, GAMING_COMMENTARY_PROFILE})

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "game": ("game", "game_name", "game_title"),
    "event": ("event", "game_event", "event_description", "specific_event"),
    "creator_angle": ("creator_angle", "unique_angle", "angle"),
    "commentary": ("commentary", "creator_commentary", "narration"),
    "payoff": ("payoff", "conclusion", "takeaway"),
}
_LABEL_ALIASES = {
    "game": "game",
    "game name": "game",
    "game title": "game",
    "event": "event",
    "specific event": "event",
    "game event": "event",
    "creator angle": "creator_angle",
    "unique angle": "creator_angle",
    "angle": "creator_angle",
    "commentary": "commentary",
    "creator commentary": "commentary",
    "narration": "commentary",
    "payoff": "payoff",
    "conclusion": "payoff",
    "takeaway": "payoff",
}
_STOPWORDS = frozenset({
    "about", "after", "again", "against", "and", "are", "because", "before",
    "being", "but", "can", "could", "did", "does", "for", "from", "get",
    "got", "had", "has", "have", "how", "into", "its", "just", "like",
    "made", "make", "more", "most", "not", "our", "out", "over", "player",
    "that", "the", "their", "then", "there", "this", "through", "under",
    "was", "were", "what", "when", "where", "which", "while", "with",
    "would", "you", "your",
})


def validate_content_profile(value: object) -> str:
    """Normalize and validate an explicit editorial profile name."""
    profile = str(value or GENERAL_PROFILE).strip().lower()
    if profile not in SUPPORTED_CONTENT_PROFILES:
        raise ValueError(
            f"unsupported content profile {value!r}; choose from "
            f"{', '.join(sorted(SUPPORTED_CONTENT_PROFILES))}"
        )
    return profile


def _nonempty(value: object, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].strip()


def _metadata_field(metadata: Mapping[str, Any], field: str) -> str:
    for key in _FIELD_ALIASES[field]:
        value = _nonempty(metadata.get(key))
        if value:
            return value
    return ""


def parse_gaming_brief(
    context: str,
    source_metadata: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Read only explicitly labelled brief fields; do not guess missing facts."""
    values = {field: "" for field in _FIELD_ALIASES}
    for line in str(context or "").splitlines():
        match = re.match(r"^\s*([A-Za-z][A-Za-z ]{0,40})\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group(1)).strip().lower()
        field = _LABEL_ALIASES.get(label)
        value = _nonempty(match.group(2))
        if field and value and not values[field]:
            values[field] = value

    metadata = source_metadata if isinstance(source_metadata, Mapping) else {}
    for field in values:
        if not values[field]:
            values[field] = _metadata_field(metadata, field)
    return values


def append_source_metadata_fields(
    context: str,
    source_metadata: Mapping[str, Any] | None,
) -> str:
    """Add explicit asset-side brief fields to the planner context, without guesses."""
    metadata = source_metadata if isinstance(source_metadata, Mapping) else {}
    current = parse_gaming_brief(context, None)
    labels = (
        ("game", "Game"),
        ("event", "Event"),
        ("creator_angle", "Creator angle"),
        ("commentary", "Commentary"),
        ("payoff", "Payoff"),
    )
    additions = [
        f"{label}: {value}"
        for field, label in labels
        if not current[field] and (value := _metadata_field(metadata, field))
    ]
    if not additions:
        return str(context or "")
    base = str(context or "").rstrip()
    return (base + "\n" if base else "") + "\n".join(additions)


def gaming_profile_context(context: str, content_profile: object) -> str:
    """Add task-specific creative constraints for gaming commentary plans."""
    profile = validate_content_profile(content_profile)
    source = str(context or "").strip()
    if profile != GAMING_COMMENTARY_PROFILE:
        return source
    marker = "[EDIT FACTORY GAMING COMMENTARY PROFILE]"
    if marker.lower() in source.lower():
        return source
    guidance = (
        f"{marker}\n"
        "Make original gaming commentary the point of the edit, not decorative text over gameplay. "
        "Use the supplied game, event, creator angle, commentary, and payoff only when provided. "
        "Ground the opening hook and payoff in analyzed source footage; never invent a kill, clutch, "
        "win, strategy, reaction, or outcome. Tie commentary to a visible action or decision, explain "
        "why it mattered, and end with the supported result or a clear takeaway. If footage does not "
        "support a claim, leave the claim unresolved and require human review. A creator angle is "
        "user-provided intent, not machine-verified originality."
    )
    return f"{source}\n\n{guidance}".strip()


def _tokens(value: object) -> set[str]:
    return {
        word for word in re.findall(r"[a-z0-9']+", str(value or "").lower())
        if len(word) >= 3 and word not in _STOPWORDS
    }


def build_gaming_editorial_report(
    *,
    content_profile: object,
    topic: str,
    context: str,
    source_metadata: Mapping[str, Any] | None,
    blueprint: Mapping[str, Any],
    footage_evidence: Mapping[str, Any],
    clip_evidence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a machine-readable gaming-content gate; scores are evidence, not guarantees."""
    profile = validate_content_profile(content_profile)
    if profile != GAMING_COMMENTARY_PROFILE:
        return {
            "schema_version": 1,
            "enabled": False,
            "content_profile": profile,
            "status": "not_applicable",
            "publish_blocked": False,
            "human_review_required": False,
            "missing_fields": [],
            "block_reasons": [],
            "checks": {},
        }

    fields = parse_gaming_brief(context, source_metadata)
    required = ("game", "event", "creator_angle", "commentary", "payoff")
    missing = [field for field in required if not fields.get(field)]
    scenes = [
        scene for scene in (footage_evidence.get("top_scenes") or [])
        if isinstance(scene, Mapping)
    ]
    source_text = " ".join(
        str(scene.get(key) or "")
        for scene in scenes
        for key in ("description", "transcript", "objects", "text")
    )
    source_tokens = _tokens(source_text)
    commentary_tokens = _tokens(fields.get("commentary", ""))
    overlap = sorted(source_tokens & commentary_tokens)

    def evidence_for(*purposes: str) -> list[dict[str, Any]]:
        wanted = {item.casefold() for item in purposes}
        return [
            dict(item) for item in clip_evidence
            if str(item.get("purpose") or "").strip().casefold() in wanted
        ]

    hook_rows = evidence_for("hook")
    payoff_rows = evidence_for("payoff", "climax", "punchline", "final impact")
    hook_supported = bool(hook_rows) and all(row.get("status") == "supported" for row in hook_rows)
    payoff_supported = bool(payoff_rows) and any(row.get("status") == "supported" for row in payoff_rows)
    commentary_reviewed = source_metadata.get("commentary_reviewed") is True if isinstance(source_metadata, Mapping) else False
    commentary_grounded = len(overlap) >= 2 or commentary_reviewed

    checks = {
        "game_identified": bool(fields.get("game")),
        "specific_event_identified": bool(fields.get("event")),
        "creator_angle_provided": bool(fields.get("creator_angle")),
        "commentary_provided": bool(fields.get("commentary")),
        "payoff_provided": bool(fields.get("payoff")),
        "source_scenes_available": bool(scenes),
        "hook_has_supported_source_evidence": hook_supported,
        "payoff_has_supported_source_evidence": payoff_supported,
        "commentary_has_source_overlap_or_explicit_human_review": commentary_grounded,
        "angle_originality_machine_verifiable": False,
    }
    block_reasons = [f"required gaming brief field is missing: {field}" for field in missing]
    if not scenes:
        block_reasons.append("no analyzed gameplay scenes are available to ground the story")
    if not hook_supported:
        block_reasons.append("the opening hook lacks supported source-scene evidence")
    if not payoff_supported:
        block_reasons.append("the payoff lacks supported source-scene evidence")
    if fields.get("commentary") and not commentary_grounded:
        block_reasons.append(
            "commentary has fewer than two meaningful lexical matches with scene evidence; "
            "review it or set commentary_reviewed=true after checking the footage"
        )

    blocked = bool(block_reasons)
    return {
        "schema_version": 1,
        "enabled": True,
        "content_profile": profile,
        "status": "blocked" if blocked else "eligible_for_human_review",
        "topic": _nonempty(topic),
        "brief": fields,
        "checks": checks,
        "source_scene_count": len(scenes),
        "commentary_source_overlap_terms": overlap,
        "critical_beats": {"hook": hook_rows, "payoff": payoff_rows},
        "missing_fields": missing,
        "block_reasons": block_reasons,
        "publish_blocked": blocked,
        "human_review_required": True,
        "originality_limit": (
            "Creator-angle uniqueness and platform monetization eligibility cannot be established "
            "automatically; inspect the final video and game publisher's commercial-use terms."
        ),
    }


__all__ = [
    "GENERAL_PROFILE",
    "GAMING_COMMENTARY_PROFILE",
    "SUPPORTED_CONTENT_PROFILES",
    "append_source_metadata_fields",
    "build_gaming_editorial_report",
    "gaming_profile_context",
    "parse_gaming_brief",
    "validate_content_profile",
]
