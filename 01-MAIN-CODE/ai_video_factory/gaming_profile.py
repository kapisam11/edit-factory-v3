"""Gaming-specific editorial brief and evidence report for V3.

This module intentionally does not infer game mechanics or outcomes. The game
and event brief come from the creator; claims about actions must be supported
by analyzed footage and the final cut still requires human review.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

GAMING_ANGLES: dict[str, dict[str, Any]] = {
    "clutch": {
        "label": "Clutch / epic play",
        "sequence": [
            "open close to the decisive action",
            "briefly establish the stakes without hiding the gameplay",
            "keep the key action visible and understandable",
            "explain the observed decision and why it mattered",
            "end on the evidenced outcome or a genuine reaction",
        ],
    },
    "strategy": {
        "label": "Strategy / educational commentary",
        "sequence": [
            "introduce one specific problem visible in the gameplay",
            "show the relevant positioning, timing, resource, or decision evidence",
            "explain the choice and a plausible alternative without inventing unseen events",
            "show the observed result",
            "finish with a useful, footage-supported takeaway",
        ],
    },
    "funny": {
        "label": "Funny moment / story",
        "sequence": [
            "set up only the context needed to understand the moment",
            "let the original action and reaction remain clear",
            "preserve comic timing; do not narrate over the punchline",
            "add creator perspective without fabricating reactions or dialogue",
            "end on the actual payoff rather than stretching the clip",
        ],
    },
    "challenge": {
        "label": "Challenge / attempt",
        "sequence": [
            "state the creator-supplied challenge or rule",
            "show the starting situation and constraint",
            "retain the meaningful attempts and turning point",
            "explain setbacks only when visible or supplied as verified context",
            "show the real result; do not invent success or failure",
        ],
    },
    "storytelling": {
        "label": "Gaming story",
        "sequence": [
            "open on a verified moment that raises a clear question",
            "give only the context needed to follow the gameplay",
            "show a real conflict, change, or decision",
            "build toward an evidenced turning point",
            "deliver the actual outcome or explicitly leave it unresolved",
        ],
    },
}

CRITICAL_PURPOSES = frozenset(
    {"hook", "payoff", "punchline", "climax", "final impact"}
)


def validate_gaming_brief(
    game_name: str,
    event_brief: str,
    angle: str,
) -> list[str]:
    errors: list[str] = []
    if not str(game_name or "").strip():
        errors.append("game name is required for the gaming profile")
    if not str(event_brief or "").strip():
        errors.append("event brief is required for the gaming profile")
    if str(angle or "").strip().lower() not in GAMING_ANGLES:
        errors.append("gaming angle must be one of: " + ", ".join(sorted(GAMING_ANGLES)))
    if len(str(game_name or "").strip()) > 120:
        errors.append("game name must be 120 characters or fewer")
    if len(str(event_brief or "").strip()) > 500:
        errors.append("event brief must be 500 characters or fewer")
    return errors


def build_gaming_planning_context(
    *,
    game_name: str,
    event_brief: str,
    angle: str,
) -> str:
    """Return bounded creative direction that can be appended to the V3 context."""
    selected = str(angle or "").strip().lower()
    if selected not in GAMING_ANGLES:
        raise ValueError("unsupported gaming angle")
    if validate_gaming_brief(game_name, event_brief, selected):
        raise ValueError("gaming profile requires a game, event brief, and valid creator angle")
    sequence = GAMING_ANGLES[selected]["sequence"]
    steps = "\n".join(f"{index}. {value}" for index, value in enumerate(sequence, start=1))
    return (
        "GAMING COMMENTARY PROFILE — CREATIVE CONSTRAINTS, NOT FACTUAL EVIDENCE\n"
        f"Game supplied by creator: {str(game_name).strip()}\n"
        f"Creator's event brief (must still be checked against the footage): {str(event_brief).strip()}\n"
        f"Creator angle: {GAMING_ANGLES[selected]['label']}\n"
        f"Editorial sequence:\n{steps}\n"
        "Use analyzed source footage as evidence, not the topic/title alone. Do not invent kills, "
        "wins, losses, weapons, player names, reactions, dialogue, strategies, or game events. "
        "Distinguish a visible action from an interpretation of why it worked. Tie every important "
        "commentary claim to a specific supplied scene or verified creator context. If evidence is "
        "missing or ambiguous, reduce the claim and require human review. Keep decisive gameplay "
        "visible; overlays, music, and transitions must not obscure the action. The finished cut "
        "must be reviewed by a person before publishing."
    )


def build_gaming_profile_report(
    *,
    game_name: str,
    event_brief: str,
    angle: str,
    footage_evidence: Mapping[str, Any] | None,
    clip_evidence: Sequence[Mapping[str, Any]] | None = None,
    source_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a traceable report without treating topic text as proof of gameplay."""
    selected = str(angle or "").strip().lower()
    errors = validate_gaming_brief(game_name, event_brief, selected)
    clips = [dict(item) for item in (clip_evidence or []) if isinstance(item, Mapping)]
    critical_unverified = [
        {
            "clip_index": item.get("clip_index"),
            "purpose": item.get("purpose", ""),
            "status": item.get("status", "unsupported"),
            "reason": item.get("reason", ""),
        }
        for item in clips
        if str(item.get("purpose", "")).strip().lower() in CRITICAL_PURPOSES
        and str(item.get("status", "unsupported")).strip().lower() != "supported"
    ]
    footage = dict(footage_evidence or {})
    source = dict(source_metadata or {})
    origin = str(source.get("asset_origin") or "unknown").strip().lower()
    commercial_use = str(source.get("commercial_use") or "review_required").strip().lower()
    attribution_required = source.get("attribution_required")
    if isinstance(attribution_required, str):
        attribution_required = attribution_required.strip().lower() in {"1", "true", "yes", "on"}
    if attribution_required is not True:
        attribution_required = False
    attribution = str(source.get("attribution") or "").strip()
    rights_basis = str(source.get("rights_basis") or "review_required").strip().lower()
    rights_evidence_url = str(source.get("evidence_url") or "").strip()
    license_url = str(source.get("license_url") or "").strip()

    evidence_counts = {
        status: sum(
            1 for item in clips
            if str(item.get("status", "unsupported")).strip().lower() == status
        )
        for status in ("supported", "weak", "unsupported")
    }
    rights_items: list[str] = []
    if origin == "unknown":
        rights_items.append("asset origin has not been declared")
    if commercial_use != "allowed":
        rights_items.append("commercial use has not been explicitly verified as allowed")
    if rights_basis in {"", "review_required", "unverified", "license_identified"}:
        rights_items.append("rights basis is not explicitly cleared")
    if rights_basis not in {"owned", "public_domain"} and not rights_evidence_url:
        rights_items.append("permission/license evidence URL is missing")
    if rights_basis in {"commercial_license", "cc_license"} and not license_url:
        rights_items.append("license URL is missing")
    if attribution_required and not attribution:
        rights_items.append("required attribution text is missing")

    if selected in GAMING_ANGLES:
        sequence = list(GAMING_ANGLES[selected]["sequence"])
        angle_label = str(GAMING_ANGLES[selected]["label"])
    else:
        sequence = []
        angle_label = selected or "unspecified"

    return {
        "schema_version": "1.0.0",
        "enabled": True,
        "content_profile": "gaming",
        "game_name": str(game_name or "").strip(),
        "event_brief": str(event_brief or "").strip(),
        "event_brief_status": "creator_supplied_not_proof_of_event",
        "creator_angle": selected,
        "creator_angle_label": angle_label,
        "editorial_sequence": sequence,
        "claim_policy": {
            "require_footage_or_verified_context": True,
            "do_not_invent_actions_or_outcomes": True,
            "keep_decisive_gameplay_visible": True,
            "human_review_required": True,
        },
        "footage_evidence": {
            "scene_count": int(footage.get("scene_count") or 0),
            "analyzed_scene_count": len(footage.get("top_scenes") or []),
            "evidence_source": "analyzed input footage",
        },
        "clip_evidence": {
            "clip_count": len(clips),
            "status_counts": evidence_counts,
            "critical_unverified_clips": critical_unverified,
            "passed": not critical_unverified and not errors,
        },
        "rights_provenance": {
            "asset_origin": origin,
            "rights_basis": rights_basis,
            "commercial_use": commercial_use,
            "rights_evidence_url_present": bool(rights_evidence_url),
            "license_url_present": bool(license_url),
            "attribution_required": attribution_required,
            "attribution_present": bool(attribution),
            "declared_by": str(source.get("declared_by") or "").strip(),
            "declared_at": str(source.get("declared_at") or "").strip(),
            "human_approval_status": "required",
            "unresolved_items": rights_items,
            "publish_blocked": bool(rights_items),
        },
        "human_review_status": "required",
        "validation_errors": errors,
        "publish_blocked": bool(errors or critical_unverified or rights_items),
        "limitations": [
            "Scene analysis is evidence for review, not guaranteed game-mechanics recognition.",
            "The creator-supplied game/event brief is not proof that the event occurred.",
            "Automated checks cannot certify monetization or platform eligibility.",
        ],
    }


__all__ = [
    "CRITICAL_PURPOSES",
    "GAMING_ANGLES",
    "build_gaming_planning_context",
    "build_gaming_profile_report",
    "validate_gaming_brief",
]
