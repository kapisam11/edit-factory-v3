"""Explicit human-review contract for artistic and publish decisions."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

REQUIRED_KEYS=("hook","pacing","visual_relevance","caption_readability","audio","rights","metadata")

@dataclass(frozen=True)
class HumanReview:
    reviewer: str
    approved: bool
    scores: dict[str,int]
    notes: str=""
    reviewed_at: str=""
    def to_dict(self)->dict[str,Any]:
        return asdict(self)

def create_review(reviewer: str, scores: Mapping[str,int], *, approved: bool=False, notes: str="", now: datetime|None=None)->HumanReview:
    when=now or datetime.now(timezone.utc)
    return HumanReview(str(reviewer).strip(),bool(approved),{str(k):int(v) for k,v in scores.items()},str(notes).strip(),when.isoformat().replace("+00:00","Z"))

def validate_review(review: HumanReview)->list[str]:
    errors=[]
    if not review.reviewer: errors.append("reviewer is required")
    if not review.reviewed_at: errors.append("reviewed_at is required")
    for key in REQUIRED_KEYS:
        value=review.scores.get(key)
        if value is None: errors.append(f"missing review score: {key}")
        elif not 1 <= int(value) <= 5: errors.append(f"review score out of range: {key}")
    if review.approved and errors: errors.append("review cannot be approved while invalid")
    return errors

def review_gate(review: HumanReview|Mapping[str,Any]|None)->dict[str,Any]:
    if review is None: return {"status":"review_required","publish_blocked":True,"errors":["human review has not been completed"]}
    item=review if isinstance(review,HumanReview) else HumanReview(
        reviewer=str(review.get("reviewer") or ""),
        approved=bool(review.get("approved",False)),
        scores={str(k):int(v) for k,v in (review.get("scores") or {}).items()},
        notes=str(review.get("notes") or ""),
        reviewed_at=str(review.get("reviewed_at") or ""),
    )
    errors=validate_review(item)
    return {"status":"approved" if item.approved and not errors else "review_required","publish_blocked":not item.approved or bool(errors),"errors":errors}

__all__=["HumanReview","REQUIRED_KEYS","create_review","review_gate","validate_review"]
