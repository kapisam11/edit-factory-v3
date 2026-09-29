"""Deterministic factual-claim review for generated upload metadata."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

@dataclass(frozen=True)
class Claim:
    text: str
    kind: str

_DATE_RE = re.compile(r"\b(?:19|20)\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}\b")
_NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?%?\b")

def extract_claims(text: str) -> list[Claim]:
    value = re.sub(r"\s+"," ",str(text or "")).strip()
    claims=[]
    seen=set()
    for regex, kind in ((_DATE_RE,"date"),(_NUMBER_RE,"number")):
        for match in regex.finditer(value):
            token=match.group(0)
            if token not in seen:
                seen.add(token); claims.append(Claim(token,kind))
    return claims

def review_claims(text: str, evidence: Sequence[Mapping[str,Any]]|None=None, *, fact_reviewed: bool=False) -> dict[str,Any]:
    claims=extract_claims(text)
    evidence_text=" ".join(str(item.get("text") or "") for item in (evidence or []))
    unsupported=[claim.text for claim in claims if claim.text not in evidence_text]
    reviewed=bool(fact_reviewed)
    status="verified" if reviewed or not unsupported else "manual_review"
    return {"status":status,"claims":[{"text":c.text,"kind":c.kind} for c in claims],"unsupported_claims":unsupported,"fact_review_required":bool(unsupported) or (bool(claims) and not reviewed),"evidence_count":len(evidence or [])}

def metadata_fact_gate(title: str, description: str, *, evidence: Sequence[Mapping[str,Any]]|None=None, fact_reviewed: bool=False) -> dict[str,Any]:
    report=review_claims(f"{title}\n{description}",evidence,fact_reviewed=fact_reviewed)
    return {**report,"publish_blocked":report["status"]!="verified","text_review_scope":"numeric/date claims only; semantic factuality still needs human/source review"}

__all__=["Claim","extract_claims","metadata_fact_gate","review_claims"]
