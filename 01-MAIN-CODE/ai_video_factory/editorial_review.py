from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


class EditorialReviewError(ValueError):
    """Raised when editorial input is invalid or stale."""


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_caption_cues(cues: Sequence[Mapping[str, Any]], *, media_duration: float | None = None, max_chars: int = 120) -> list[dict[str, Any]]:
    """Validate timed captions without silently changing their timing."""
    if isinstance(cues, (str, bytes)) or not isinstance(cues, Sequence):
        raise EditorialReviewError("caption cues must be a sequence")
    if max_chars < 1:
        raise EditorialReviewError("max_chars must be positive")
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_end = 0.0
    for index, raw in enumerate(cues):
        if not isinstance(raw, Mapping):
            raise EditorialReviewError(f"caption cue {index} must be an object")
        cue = dict(raw)
        cue_id = str(cue.get("id", index)).strip()
        text = str(cue.get("text", "")).strip()
        try:
            start, end = float(cue["start"]), float(cue["end"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise EditorialReviewError(f"caption cue {cue_id} needs numeric start/end") from exc
        if not cue_id or cue_id in seen:
            raise EditorialReviewError("caption cue IDs must be non-empty and unique")
        if not text or len(text) > max_chars:
            raise EditorialReviewError(f"caption cue {cue_id} text is empty or too long")
        if not all(math.isfinite(value) for value in (start, end)) or start < 0 or end <= start:
            raise EditorialReviewError(f"caption cue {cue_id} has invalid timing")
        if start < previous_end - 0.001:
            raise EditorialReviewError(f"caption cue {cue_id} overlaps the previous cue")
        if media_duration is not None and end > float(media_duration) + 0.001:
            raise EditorialReviewError(f"caption cue {cue_id} exceeds media duration")
        cue.update({"id": cue_id, "text": text, "start": round(start, 3), "end": round(end, 3)})
        output.append(cue)
        seen.add(cue_id)
        previous_end = end
    return output


def apply_caption_edits(cues: Sequence[Mapping[str, Any]], edits: Sequence[Mapping[str, Any]], *, expected_revision: str, media_duration: float | None = None, max_chars: int = 120) -> dict[str, Any]:
    """Apply targeted edits only when the supplied revision matches the current cues."""
    original = validate_caption_cues(cues, media_duration=media_duration, max_chars=max_chars)
    if not expected_revision or expected_revision != _hash(original):
        raise EditorialReviewError("caption revision is stale; reload before saving edits")
    by_id = {cue["id"]: dict(cue) for cue in original}
    changed: set[str] = set()
    for edit in edits:
        if not isinstance(edit, Mapping):
            raise EditorialReviewError("each caption edit must be an object")
        cue_id = str(edit.get("id", "")).strip()
        if cue_id not in by_id:
            raise EditorialReviewError(f"unknown caption cue ID: {cue_id}")
        if cue_id in changed:
            raise EditorialReviewError(f"duplicate edit for caption cue: {cue_id}")
        changed.add(cue_id)
        for key in ("text", "start", "end"):
            if key in edit:
                by_id[cue_id][key] = edit[key]
    updated = validate_caption_cues([by_id[cue["id"]] for cue in original], media_duration=media_duration, max_chars=max_chars)
    return {"cues": updated, "revision": _hash(updated), "changed_ids": sorted(changed), "changed": bool(changed)}


def build_editorial_review(*, job_id: str, candidates: Sequence[Mapping[str, Any]], captions: Sequence[Mapping[str, Any]] = (), media_duration: float | None = None, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create an auditable review manifest which starts unapproved."""
    safe_id = str(job_id).strip()
    if not safe_id or any(char in safe_id for char in "/\\"):
        raise EditorialReviewError("job_id must be a non-empty identifier, not a path")
    normalized_candidates = []
    seen: set[str] = set()
    for index, raw in enumerate(candidates):
        if not isinstance(raw, Mapping):
            raise EditorialReviewError(f"candidate {index} must be an object")
        item = dict(raw)
        item_id = str(item.get("id", index)).strip()
        try:
            start, end = float(item.get("start", 0)), float(item.get("end", 0))
            score = float(item.get("score", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise EditorialReviewError(f"candidate {item_id} has invalid numeric fields") from exc
        if not item_id or item_id in seen or not all(math.isfinite(value) for value in (start, end, score)) or start < 0 or end <= start:
            raise EditorialReviewError(f"candidate {item_id} has invalid ID, score, or source bounds")
        if media_duration is not None and end > float(media_duration) + 0.001:
            raise EditorialReviewError(f"candidate {item_id} exceeds media duration")
        item.update({"id": item_id, "start": round(start, 3), "end": round(end, 3), "score": score})
        normalized_candidates.append(item)
        seen.add(item_id)
    normalized_captions = validate_caption_cues(captions, media_duration=media_duration)
    payload = {
        "schema_version": 1, "job_id": safe_id, "status": "needs_review",
        "created_at": datetime.now(timezone.utc).isoformat(), "approved_at": None,
        "approved_revision": None, "metadata": dict(metadata or {}),
        "candidates": normalized_candidates, "captions": normalized_captions,
    }
    payload["revision"] = _hash({key: payload[key] for key in ("job_id", "metadata", "candidates", "captions")})
    return payload


def approve_editorial_review(review: Mapping[str, Any], *, expected_revision: str) -> dict[str, Any]:
    """Approve the exact revision shown to the operator."""
    payload = dict(review)
    actual = _hash({key: payload.get(key, {} if key == "metadata" else []) for key in ("job_id", "metadata", "candidates", "captions")})
    if payload.get("status") not in {"needs_review", "approved"} or not expected_revision or expected_revision != actual or expected_revision != payload.get("revision"):
        raise EditorialReviewError("review changed since it was displayed; reload and review again")
    payload["status"] = "approved"
    payload["approved_revision"] = actual
    payload["approved_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def require_approved_review(review: Mapping[str, Any], *, revision: str) -> None:
    """Fail closed unless the exact, unmodified revision was explicitly approved."""
    if review.get("status") != "approved":
        raise EditorialReviewError("human editorial approval is required before rendering")
    actual = _hash({key: review.get(key, {} if key == "metadata" else []) for key in ("job_id", "metadata", "candidates", "captions")})
    if not revision or revision != actual or revision != review.get("revision") or revision != review.get("approved_revision"):
        raise EditorialReviewError("approved review revision does not match render inputs")


def save_review_atomic(path: str | os.PathLike[str], review: Mapping[str, Any]) -> str:
    """Persist a manifest through a same-directory temporary file and atomic replace."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(dict(review), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return str(destination)
