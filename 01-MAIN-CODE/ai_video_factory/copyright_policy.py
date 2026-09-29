"""Deterministic copyright/provenance gate with optional external verification.

This module never claims that an asset is legally safe. It provides a
fail-closed contract that requires explicit rights evidence and, when enabled,
an external verification provider result before publishing.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, Sequence


class CopyrightStatus(str, Enum):
    NOT_CHECKED = "not_checked"
    VERIFIED = "verified"
    BLOCKED = "blocked"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class CopyrightEvidence:
    asset_id: str
    sha256: str
    rights_status: str
    provider: str = ""
    provider_reference: str = ""
    provider_status: CopyrightStatus = CopyrightStatus.NOT_CHECKED
    notes: str = ""


class CopyrightVerificationProvider(Protocol):
    name: str

    def verify(self, asset_id: str, sha256: str, source_url: str = "") -> CopyrightEvidence:
        ...


def fingerprint_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ExactMatchProvider:
    """Local exact-fingerprint deny/allow provider for deterministic CI tests."""

    name = "exact-local"

    def __init__(self, *, blocked_hashes: Sequence[str] = (), allowed_hashes: Sequence[str] = ()) -> None:
        self.blocked = {str(value).lower() for value in blocked_hashes}
        self.allowed = {str(value).lower() for value in allowed_hashes}

    def verify(self, asset_id: str, sha256: str, source_url: str = "") -> CopyrightEvidence:
        digest = str(sha256).lower()
        if digest in self.blocked:
            return CopyrightEvidence(
                asset_id=asset_id,
                sha256=digest,
                rights_status="review_required",
                provider=self.name,
                provider_status=CopyrightStatus.BLOCKED,
                notes="Exact fingerprint is present in the configured blocked set.",
            )
        if digest in self.allowed:
            return CopyrightEvidence(
                asset_id=asset_id,
                sha256=digest,
                rights_status="explicit_permission",
                provider=self.name,
                provider_status=CopyrightStatus.VERIFIED,
                notes="Exact fingerprint is present in the configured allow set.",
            )
        return CopyrightEvidence(
            asset_id=asset_id,
            sha256=digest,
            rights_status="review_required",
            provider=self.name,
            provider_status=CopyrightStatus.MANUAL_REVIEW,
            notes="No exact fingerprint decision exists in the configured local database.",
        )


def copyright_gate(
    records: Sequence[CopyrightEvidence],
    *,
    require_provider: bool = False,
) -> dict[str, object]:
    unresolved = []
    for item in records:
        rights_ok = str(item.rights_status).strip().lower() in {
            "owned",
            "explicit_permission",
            "commercial_license",
            "public_domain",
            "cc_license",
        }
        provider_ok = item.provider_status is CopyrightStatus.VERIFIED
        if not rights_ok or (require_provider and not provider_ok):
            unresolved.append(item.asset_id)
    return {
        "status": "cleared" if not unresolved else "review_required",
        "publish_blocked": bool(unresolved),
        "unresolved_assets": unresolved,
        "provider_required": require_provider,
    }


__all__ = [
    "CopyrightEvidence",
    "CopyrightStatus",
    "CopyrightVerificationProvider",
    "ExactMatchProvider",
    "copyright_gate",
    "fingerprint_file",
]
