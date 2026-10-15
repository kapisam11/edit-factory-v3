"""Evidence-based media rights gate; it does not make legal conclusions."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

CLEARED_BASES = frozenset({"owned","explicit_permission","commercial_license","public_domain","cc_license"})

@dataclass(frozen=True)
class RightsEvidence:
    asset_id: str
    source: str
    rights_basis: str
    evidence_url: str = ""
    license_url: str = ""
    declared_by: str = ""
    declared_at: str = ""
    attribution: str = ""
    asset_origin: str = "unknown"
    commercial_use: str = "review_required"
    permitted_uses: str = ""
    attribution_required: bool = False

def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

def build_rights_evidence(raw: Mapping[str, Any]) -> RightsEvidence:
    return RightsEvidence(
        asset_id=str(raw.get("asset_id") or raw.get("id") or "asset"),
        source=str(raw.get("source") or "unknown"),
        rights_basis=str(raw.get("rights_basis") or raw.get("rights_status") or "review_required").strip().lower(),
        evidence_url=str(raw.get("evidence_url") or raw.get("rights_evidence_url") or "").strip(),
        license_url=str(raw.get("license_url") or "").strip(),
        declared_by=str(raw.get("declared_by") or "").strip(),
        declared_at=str(raw.get("declared_at") or "").strip(),
        attribution=str(raw.get("attribution") or "").strip(),
        asset_origin=str(raw.get("asset_origin") or "unknown").strip().lower(),
        commercial_use=str(raw.get("commercial_use") or "review_required").strip().lower(),
        permitted_uses=str(raw.get("permitted_uses") or "").strip(),
        attribution_required=_bool_value(raw.get("attribution_required")),
    )

def validate_rights_evidence(
    evidence: RightsEvidence,
    *,
    require_commercial_use: bool = False,
) -> list[str]:
    errors: list[str] = []
    if evidence.rights_basis not in CLEARED_BASES:
        errors.append("rights basis is not explicitly cleared")
    if evidence.rights_basis not in {"owned","public_domain"} and not evidence.evidence_url:
        errors.append("permission or license evidence URL is missing")
    if evidence.rights_basis in {"commercial_license","cc_license"} and not evidence.license_url:
        errors.append("license URL is required for licensed media")
    if not evidence.declared_by:
        errors.append("rights declaration has no declared_by identity")
    if not evidence.declared_at:
        errors.append("rights declaration has no declared_at timestamp")
    if evidence.declared_at:
        try:
            declared_at = datetime.fromisoformat(evidence.declared_at.replace("Z", "+00:00"))
            if declared_at.tzinfo is None:
                errors.append("declared_at must include a timezone")
            elif declared_at > datetime.now(timezone.utc):
                errors.append("declared_at cannot be in the future")
        except ValueError:
            errors.append("declared_at is not an ISO-8601 timestamp")
    if evidence.attribution_required and not evidence.attribution:
        errors.append("required attribution text is missing")
    if require_commercial_use:
        if evidence.asset_origin not in {"original", "ai_generated", "licensed", "third_party", "user_provided"}:
            errors.append("asset origin is not explicitly declared")
        if evidence.commercial_use != "allowed":
            errors.append("commercial use is not explicitly declared as allowed")
        if evidence.permitted_uses and evidence.commercial_use == "not_allowed":
            errors.append("declared permitted uses prohibit commercial use")
    return errors

def rights_gate(
    records: Sequence[Mapping[str, Any]],
    *,
    strict: bool = True,
    require_commercial_use: bool = False,
) -> dict[str, Any]:
    checked, unresolved = [], []
    for raw in records:
        evidence = build_rights_evidence(raw)
        errors = validate_rights_evidence(evidence, require_commercial_use=require_commercial_use)
        item = {
            "asset_id": evidence.asset_id,
            "source": evidence.source,
            "rights_basis": evidence.rights_basis,
            "asset_origin": evidence.asset_origin,
            "commercial_use": evidence.commercial_use,
            "permitted_uses": evidence.permitted_uses,
            "attribution_required": evidence.attribution_required,
            "attribution_present": bool(evidence.attribution),
            "evidence_url_present": bool(evidence.evidence_url),
            "license_url_present": bool(evidence.license_url),
            "declared_by": evidence.declared_by,
            "declared_at": evidence.declared_at,
            "errors": errors,
        }
        checked.append(item)
        if errors:
            unresolved.append(item)
    # Rights failures are always publishing blockers. The strict flag is retained
    # for API compatibility but cannot disable a safety-critical gate.
    return {
        "status": "cleared" if not unresolved else "review_required",
        "publish_blocked": bool(unresolved),
        "checked": checked,
        "evidence_contract": "explicit rights basis + evidence + declaration identity/time",
        "commercial_use_required": bool(require_commercial_use),
    }

def make_declaration(
    *,
    asset_id: str,
    source: str,
    rights_basis: str,
    evidence_url: str = "",
    license_url: str = "",
    declared_by: str = "local-user",
    now: datetime | None = None,
    asset_origin: str = "unknown",
    commercial_use: str = "review_required",
    permitted_uses: str = "",
    attribution_required: bool = False,
    attribution: str = "",
) -> dict[str, Any]:
    when = now or datetime.now(timezone.utc)
    return {
        "asset_id": asset_id,
        "source": source,
        "rights_basis": rights_basis.strip().lower(),
        "evidence_url": evidence_url,
        "license_url": license_url,
        "declared_by": declared_by,
        "declared_at": when.isoformat().replace("+00:00", "Z"),
        "asset_origin": asset_origin,
        "commercial_use": commercial_use,
        "permitted_uses": permitted_uses,
        "attribution_required": attribution_required,
        "attribution": attribution,
    }

__all__=["CLEARED_BASES","RightsEvidence","build_rights_evidence","make_declaration","rights_gate","validate_rights_evidence"]
