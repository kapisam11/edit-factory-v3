"""Safe sidecar review edits for completed video packages.

The workbench never overwrites the source render. Reviewed captions and metadata
are stored under package/reviewed/ so the original output remains recoverable.
Changing burned-in captions still requires an explicit re-render or NLE export.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_CAPTION_CHARS = 500_000
MAX_CUES = 3000
MAX_TITLE_CHARS = 500
MAX_DESCRIPTION_CHARS = 50_000
_TIMESTAMP = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{1,3})$")
_TIME_LINE = re.compile(r"^(\d{2,}:[0-5]\d:[0-5]\d[,.]\d{1,3})\s+-->\s+(\d{2,}:[0-5]\d:[0-5]\d[,.]\d{1,3})$")
_VERSION_FILE = re.compile(r"^rev-(\d{6})\.json$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _seconds(timestamp: str) -> float:
    match = _TIMESTAMP.fullmatch(timestamp.strip())
    if not match:
        raise ValueError(f"Invalid subtitle timestamp: {timestamp!r}")
    hours, minutes, seconds, millis = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000.0


def validate_srt(value: str) -> str:
    """Validate common SubRip syntax and return a normalized SRT document."""
    if not isinstance(value, str):
        raise ValueError("captions_srt must be text")
    if len(value) > MAX_CAPTION_CHARS:
        raise ValueError(f"Subtitle file exceeds {MAX_CAPTION_CHARS} characters")
    value = value.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return ""
    blocks = [block.strip() for block in re.split(r"\n\s*\n", value) if block.strip()]
    if len(blocks) > MAX_CUES:
        raise ValueError(f"Subtitle file exceeds {MAX_CUES} cues")
    normalized: list[str] = []
    previous_start = -1.0
    for index, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.split("\n") if line.strip() or line == ""]
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if not lines or not _TIME_LINE.fullmatch(lines[0].strip()):
            raise ValueError(f"Cue {index}: expected 'HH:MM:SS,mmm --> HH:MM:SS,mmm'")
        time_match = _TIME_LINE.fullmatch(lines[0].strip())
        assert time_match is not None
        start = _seconds(time_match.group(1))
        end = _seconds(time_match.group(2))
        if end <= start:
            raise ValueError(f"Cue {index}: end time must be after start time")
        if start < previous_start:
            raise ValueError(f"Cue {index}: start times must be in ascending order")
        previous_start = start
        text_lines = [line.strip() for line in lines[1:] if line.strip()]
        if not text_lines:
            raise ValueError(f"Cue {index}: subtitle text cannot be empty")
        if any(len(line) > 500 for line in text_lines):
            raise ValueError(f"Cue {index}: subtitle line exceeds 500 characters")
        normalized.append(
            f"{index}\n{time_match.group(1).replace('.', ',')} --> {time_match.group(2).replace('.', ',')}\n"
            + "\n".join(text_lines)
        )
    return "\n\n".join(normalized) + "\n"


def _find_artifact(package: Path, names: tuple[str, ...]) -> Path | None:
    excluded = {"reviewed", "review-revisions", ".review-revisions"}
    candidates = [
        path for path in package.rglob("*")
        if path.is_file() and path.name.lower() in names
        and not (set(path.relative_to(package).parts) & excluded)
        and path.stat().st_size <= 2_000_000
    ]
    if not candidates:
        return None
    def priority(path: Path) -> tuple[int, int, str]:
        rel = path.relative_to(package).as_posix().lower()
        exact = 0 if "/" not in rel else 1
        preference = names.index(path.name.lower())
        return (exact, preference, rel)
    return sorted(candidates, key=priority)[0]


def _safe_read(path: Path | None) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path is not None else ""


def _review_dir(package: Path) -> Path:
    root = package.resolve()
    review = (root / "reviewed").resolve()
    if review.parent != root:
        raise ValueError("Invalid review directory")
    return review


def _state_path(package: Path) -> Path:
    return _review_dir(package) / "review-overrides.json"


def _load_state(package: Path) -> dict[str, Any] | None:
    path = _state_path(package)
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Saved review state is damaged; original package files were left untouched") from exc
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported review state version; original package files were left untouched")
    return state


def load_review(package: Path) -> dict[str, Any]:
    """Load the most recent review state or seed the workbench from package files."""
    state = _load_state(package)
    if state is not None:
        return state
    captions = _find_artifact(package, ("captions.srt", "subtitle.srt"))
    title = _find_artifact(package, ("title.txt",))
    description = _find_artifact(package, ("description.txt",))
    video_candidates = [
        path for path in package.rglob("*.mp4")
        if path.is_file() and "reviewed" not in path.relative_to(package).parts
        and path.stat().st_size <= 20 * 1024 * 1024 * 1024
    ]
    preferred = {"final.v3.mp4": 0, "final.mp4": 1, "final_video.mp4": 2, "output.mp4": 3}
    videos = sorted(video_candidates, key=lambda path: (
        preferred.get(path.name.lower(), 99),
        0 if path.parent == package else 1,
        path.relative_to(package).as_posix(),
    ))
    video = videos[0] if videos else None
    relative_video = video.relative_to(package).as_posix() if video else ""
    return {
        "schema_version": SCHEMA_VERSION,
        "version": 0,
        "updated_at": "",
        "title": _safe_read(title).strip(),
        "description": _safe_read(description).strip(),
        "captions_srt": _safe_read(captions),
        "source_caption_file": captions.relative_to(package).as_posix() if captions else "",
        "video_file": relative_video,
        "render_status": "unchanged",
        "history": [],
    }


def _digest(payload: Mapping[str, Any]) -> str:
    relevant = {key: payload.get(key, "") for key in ("title", "description", "captions_srt")}
    encoded = json.dumps(relevant, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def save_review(package: Path, edits: Mapping[str, Any], *, restored_from: int | None = None) -> dict[str, Any]:
    """Persist a new immutable review revision and export reviewed sidecar artifacts."""
    if not isinstance(edits, Mapping):
        raise ValueError("Review payload must be an object")
    current = load_review(package)
    title = edits.get("title", current.get("title", ""))
    description = edits.get("description", current.get("description", ""))
    captions = edits.get("captions_srt", current.get("captions_srt", ""))
    if not isinstance(title, str) or len(title) > MAX_TITLE_CHARS:
        raise ValueError(f"title must be text with at most {MAX_TITLE_CHARS} characters")
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"description must be text with at most {MAX_DESCRIPTION_CHARS} characters")
    captions = validate_srt(captions)
    version = int(current.get("version", 0)) + 1
    updated_at = _now()
    review_dir = _review_dir(package)
    revisions_dir = review_dir / "revisions"
    edits_payload = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "updated_at": updated_at,
        "title": title.strip(),
        "description": description.strip(),
        "captions_srt": captions,
        "source_caption_file": str(current.get("source_caption_file") or ""),
        "video_file": str(current.get("video_file") or ""),
        "render_status": "requires_rerender" if captions != current.get("captions_srt", "") else "metadata_only_or_no_video_change",
        "restored_from": restored_from,
    }
    digest = _digest(edits_payload)
    revisions_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(revisions_dir / f"rev-{version:06d}.json", edits_payload)
    history = list(current.get("history") or [])
    history.append({
        "version": version,
        "updated_at": updated_at,
        "sha256": digest,
        "restored_from": restored_from,
        "caption_cues": len([block for block in captions.split("\n\n") if block.strip()]),
    })
    history = history[-50:]
    state = {**edits_payload, "history": history, "sha256": digest}
    _atomic_json(_state_path(package), state)
    _atomic_text(review_dir / "captions.srt", captions)
    _atomic_text(review_dir / "title.txt", title.strip() + ("\n" if title.strip() else ""))
    _atomic_text(review_dir / "description.txt", description.strip() + ("\n" if description.strip() else ""))

    keep_versions = {int(item.get("version", 0)) for item in history}
    for path in revisions_dir.glob("rev-*.json"):
        match = _VERSION_FILE.fullmatch(path.name)
        if match and int(match.group(1)) not in keep_versions:
            try:
                path.unlink()
            except OSError:
                pass
    return state


def restore_review(package: Path, version: int) -> dict[str, Any]:
    """Restore an older revision as a new revision; history remains append-only."""
    if isinstance(version, bool) or int(version) < 1:
        raise ValueError("Review revision must be a positive integer")
    current = load_review(package)
    history_versions = {int(item.get("version", 0)) for item in current.get("history", [])}
    if int(version) not in history_versions:
        raise ValueError("Requested review revision does not exist")
    snapshot = _review_dir(package) / "revisions" / f"rev-{int(version):06d}.json"
    if not snapshot.is_file():
        raise ValueError("Requested review revision snapshot is missing")
    try:
        previous = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Requested review revision snapshot is damaged") from exc
    return save_review(package, previous, restored_from=int(version))


__all__ = ["load_review", "restore_review", "save_review", "validate_srt"]
