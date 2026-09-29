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

def build_rights_evidence(raw: Mapping[str, Any]) -> RightsEvidence:
    return RightsEvidence(
        asset_id=str(raw.get("asset_id") or raw.get("id") or "asset"),
        source=str(raw.get("source") or "unknown"),
        rights_basis=str(raw.get("rights_basis") or raw.get("rights_status") or "review_required").strip().lower(),
        evidence_url=str(raw.get("evidence_url") or raw.get("source_url") or "").strip(),
        license_url=str(raw.get("license_url") or "").strip(),
        declared_by=str(raw.get("declared_by") or "").strip(),
        declared_at=str(raw.get("declared_at") or "").strip(),
        attribution=str(raw.get("attribution") or "").strip(),
    )

def validate_rights_evidence(evidence: RightsEvidence) -> list[str]:
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
            datetime.fromisoformat(evidence.declared_at.replace("Z","+00:00"))
        except ValueError:
            errors.append("declared_at is not an ISO-8601 timestamp")
    return errors

def rights_gate(records: Sequence[Mapping[str, Any]], *, strict: bool = True) -> dict[str, Any]:
    checked, unresolved = [], []
    for raw in records:
        evidence = build_rights_evidence(raw)
        errors = validate_rights_evidence(evidence)
        item = {"asset_id":evidence.asset_id,"source":evidence.source,"rights_basis":evidence.rights_basis,"errors":errors}
        checked.append(item)
        if errors:
            unresolved.append(item)
    return {"status":"cleared" if not unresolved else "review_required","publish_blocked":bool(unresolved) and strict,"checked":checked,"evidence_contract":"explicit rights basis + evidence + declaration identity/time"}

def make_declaration(*, asset_id: str, source: str, rights_basis: str, evidence_url: str = "", license_url: str = "", declared_by: str = "local-user", now: datetime | None = None) -> dict[str, str]:
    when = now or datetime.now(timezone.utc)
    return {"asset_id":asset_id,"source":source,"rights_basis":rights_basis.strip().lower(),"evidence_url":evidence_url,"license_url":license_url,"declared_by":declared_by,"declared_at":when.isoformat().replace("+00:00","Z")}

__all__=["CLEARED_BASES","RightsEvidence","build_rights_evidence","make_declaration","rights_gate","validate_rights_evidence"]
