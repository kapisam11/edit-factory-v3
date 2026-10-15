"""Pre-publish originality checks for repeated scripts and source sequences.

The report is a review aid, not a platform-detection bypass or an automated
judgment about copyright, monetization, or the value of a recurring series.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


DEFAULT_SIMILARITY_THRESHOLD = 0.86
DEFAULT_SEQUENCE_THRESHOLD = 0.82
MIN_SCRIPT_TOKENS = 18
MAX_PACKAGES_TO_COMPARE = 25


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _read_text(path: Path) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _script_path(package: Path) -> Path | None:
    for candidate in (package / "script.txt", package / "primary" / "script.txt"):
        try:
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        except OSError:
            # One locked/private sibling must not fail the current upload.
            continue
    return None


def _package_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _game_names(profile: Mapping[str, Any]) -> list[str]:
    values = [
        str(profile.get("game_name") or ""),
        str(profile.get("game") or ""),
    ]
    return sorted(
        {value.strip() for value in values if value.strip()},
        key=len,
        reverse=True,
    )


def _canonical_script(script: str, game_names: Sequence[str]) -> list[str]:
    value = str(script or "").casefold()
    # Replacing explicit game names allows "Minecraft" vs "Fortnite" copies
    # to remain comparable without erasing the rest of the topic or storyline.
    for name in game_names:
        value = re.sub(
            rf"(?<![a-z0-9]){re.escape(name.casefold())}(?![a-z0-9])",
            " __game__ ",
            value,
        )
    return re.findall(r"[a-z0-9']+", value)


def _source_hash(package: Path) -> str:
    source = _read_json(package / "source_manifest.json")
    return str(source.get("source_sha256") or source.get("sha256") or "")


def _sequence_signature(package: Path) -> list[str]:
    evidence = _read_json(package / "clip_source_evidence.json")
    records = evidence.get("clips")
    if not isinstance(records, list):
        # Existing V3 manifests currently write a top-level list. Accept both
        # known layouts while rejecting malformed or empty evidence.
        records = evidence.get("clip_evidence")
    if not isinstance(records, list):
        # Backward-compatible canonical package format.
        try:
            raw = (package / "clip_source_evidence.json").read_text(encoding="utf-8")
            loaded = json.loads(raw)
            records = loaded if isinstance(loaded, list) else []
        except (OSError, ValueError, TypeError):
            records = []
    signature: list[str] = []
    for item in records:
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "").casefold()
        if status and status not in {"supported", "weak"}:
            continue
        try:
            start = round(float(item.get("source_start") or 0.0) * 2) / 2
            end = round(float(item.get("source_end") or 0.0) * 2) / 2
        except (TypeError, ValueError, OverflowError):
            continue
        if end <= start:
            continue
        # The source SHA-256 is checked separately before comparing sequences.
        # Time ranges therefore remain comparable even if generated scene IDs
        # differ between two analysis runs of the same recording.
        signature.append(f"{start:.1f}-{end:.1f}")
    return signature


def _similarity(first: Sequence[str], second: Sequence[str]) -> float:
    if not first or not second:
        return 0.0
    return SequenceMatcher(a=list(first), b=list(second), autojunk=False).ratio()


def _human_originality_review(
    human_review: Mapping[str, Any] | None,
) -> tuple[bool, str]:
    """Require an explicit note when an intentionally recurring format resembles a prior video."""
    review = dict(human_review or {})
    if str(review.get("status") or "").strip().lower() != "approved":
        return False, ""
    notes = str(review.get("notes") or "")
    match = re.search(r"(?is)originality-reviewed\s*:\s*(.{25,})$", notes.strip())
    if not match:
        return False, ""
    contribution = re.sub(r"\s+", " ", match.group(1)).strip()
    return True, contribution


def compare_recent_packages(
    package_dir: str | Path,
    current_script: str,
    *,
    current_topic: str = "",
    current_profile: Mapping[str, Any] | None = None,
    human_review: Mapping[str, Any] | None = None,
    max_recent: int = MAX_PACKAGES_TO_COMPARE,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    sequence_threshold: float = DEFAULT_SEQUENCE_THRESHOLD,
) -> dict[str, Any]:
    """Compare one output against recent sibling packages, never across arbitrary roots."""
    root = Path(package_dir)
    current = _canonical_script(current_script, _game_names(current_profile or {}))
    current_script_comparable = len(current) >= MIN_SCRIPT_TOKENS

    try:
        entries = list(root.parent.iterdir())
        root_resolved = root.resolve()
    except OSError:
        entries = []
        root_resolved = root
    children: list[Path] = []
    for item in entries:
        try:
            if item.is_symlink() or not item.is_dir():
                continue
            if item.resolve() == root_resolved:
                continue
            children.append(item)
        except (OSError, RuntimeError):
            # Filesystem ACLs, disappearing entries, and symlink loops are
            # irrelevant to the current job; skip them rather than fail packaging.
            continue
    children.sort(key=_package_mtime, reverse=True)
    limit = max(1, min(100, int(max_recent)))
    children = children[:limit]

    current_profile_data = dict(current_profile or {})
    current_is_gaming = bool(
        current_profile_data.get("enabled", True)
        and (current_profile_data.get("game_name") or current_profile_data.get("game"))
    )
    current_game = str(
        current_profile_data.get("game_name") or current_profile_data.get("game") or ""
    ).strip().casefold()
    current_source_hash = _source_hash(root)
    current_sequence = _sequence_signature(root)

    comparisons: list[dict[str, Any]] = []
    comparable_script_count = 0
    comparable_sequence_count = 0
    for sibling in children:
        script_path = _script_path(sibling)
        if script_path is None:
            continue
        prior_script = _read_text(script_path)
        prior_profile = _read_json(sibling / "gaming_profile.json")
        prior_is_gaming = bool(
            prior_profile.get("enabled", True)
            and (prior_profile.get("game_name") or prior_profile.get("game"))
        )
        # Avoid comparing unrelated production modes as if they were variants
        # of the same format. Gaming videos are compared with gaming videos.
        if current_is_gaming != prior_is_gaming:
            continue
        prior_game = str(prior_profile.get("game_name") or prior_profile.get("game") or "").strip()
        if current_is_gaming and current_game and prior_game and current_game != prior_game.casefold():
            game_names = [*_game_names(current_profile_data), *_game_names(prior_profile)]
        else:
            game_names = [*_game_names(current_profile_data), *_game_names(prior_profile)]
        prior_tokens = _canonical_script(prior_script, game_names)
        script_comparable = current_script_comparable and len(prior_tokens) >= MIN_SCRIPT_TOKENS
        if script_comparable:
            script_similarity: float | None = _similarity(current, prior_tokens)
            comparable_script_count += 1
        else:
            script_similarity = None
        prior_topic = sibling.name
        for meta_path in (sibling / "upload" / "metadata.json", sibling / "metadata.json"):
            meta = _read_json(meta_path)
            if meta:
                prior_topic = str(meta.get("topic") or meta.get("selected_title") or prior_topic)
                break

        prior_source_hash = _source_hash(sibling)
        prior_sequence = _sequence_signature(sibling)
        sequence_comparable = bool(
            current_source_hash
            and current_source_hash == prior_source_hash
            and len(current_sequence) >= 4
            and len(prior_sequence) >= 4
        )
        if sequence_comparable:
            sequence_similarity: float | None = _similarity(current_sequence, prior_sequence)
            comparable_sequence_count += 1
        else:
            sequence_similarity = None
        repeated_script = (
            script_similarity is not None
            and script_similarity >= float(similarity_threshold)
        )
        repeated_sequence = (
            sequence_similarity is not None
            and sequence_similarity >= float(sequence_threshold)
        )
        same_angle = bool(
            current_profile_data.get("creator_angle")
            and str(current_profile_data.get("creator_angle")).casefold()
            == str(prior_profile.get("creator_angle") or "").casefold()
        )
        flagged = repeated_script or repeated_sequence
        comparisons.append({
            "package": sibling.name,
            "topic": prior_topic,
            "game": prior_game or None,
            "script_similarity": round(script_similarity, 4) if script_similarity is not None else None,
            "source_sequence_similarity": round(sequence_similarity, 4) if sequence_similarity is not None else None,
            "same_source_media": bool(current_source_hash and current_source_hash == prior_source_hash),
            "same_creator_angle": same_angle,
            "near_duplicate_script": repeated_script,
            "reused_source_sequence": repeated_sequence,
            "review_required": flagged,
            "reason": (
                "script/storyline is unusually similar"
                if repeated_script else
                "the same source recording uses a substantially repeated sequence"
                if repeated_sequence else
                "no comparable script or source sequence"
                if not script_comparable and not sequence_comparable
                else "no strong repetition signal"
            ),
        })

    comparisons.sort(
        key=lambda item: (
            bool(item["review_required"]),
            max(
                float(item.get("script_similarity") or 0.0),
                float(item.get("source_sequence_similarity") or 0.0),
            ),
        ),
        reverse=True,
    )
    flagged = [item for item in comparisons if item["review_required"]]
    override_approved, contribution = _human_originality_review(human_review)
    publish_blocked = bool(flagged) and not override_approved
    return {
        "schema_version": "1.0.0",
        "status": (
            "approved_with_distinct_contribution"
            if flagged and override_approved
            else "review_required"
            if flagged
            else "compared"
            if comparable_script_count or comparable_sequence_count
            else "insufficient_data"
        ),
        "publish_blocked": publish_blocked,
        "reason": (
            "A recent script or source sequence is unusually similar. Add an explicit "
            "originality-reviewed note with the distinct creator contribution before publishing."
            if publish_blocked
            else "A reviewer documented the distinct contribution for a similar format."
            if flagged and override_approved
            else "No strong script or source-sequence repetition signal was found in the available packages."
            if comparable_script_count or comparable_sequence_count
            else "There was not enough comparable script or source-sequence evidence; this is not a guarantee of originality."
        ),
        "compared_package_count": len(comparisons),
        "comparable_script_count": comparable_script_count,
        "comparable_source_sequence_count": comparable_sequence_count,
        "minimum_script_tokens": MIN_SCRIPT_TOKENS,
        "script_similarity_threshold": float(similarity_threshold),
        "source_sequence_similarity_threshold": float(sequence_threshold),
        "flagged_packages": [item["package"] for item in flagged],
        "comparisons": comparisons[:20],
        "human_review_override": {
            "approved": bool(override_approved),
            "distinct_contribution": contribution,
            "required_note_prefix": "originality-reviewed: ",
        },
        "limitations": [
            "Similarity is an editorial review signal, not a copyright or monetization verdict.",
            "Shared intros, recurring visual styles, and legitimate series are not rejected by themselves.",
            "Different source footage and changed commentary can still be repetitive in ways this heuristic cannot detect.",
            "Insufficient history means there is not enough evidence for a meaningful comparison.",
        ],
    }


__all__ = [
    "DEFAULT_SEQUENCE_THRESHOLD",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "MIN_SCRIPT_TOKENS",
    "compare_recent_packages",
]
