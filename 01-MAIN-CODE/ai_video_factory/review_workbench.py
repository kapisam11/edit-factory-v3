"""Safe sidecar review edits for completed video packages.

The workbench never overwrites the source render. Reviewed captions and metadata
are stored under package/reviewed/ so the original output remains recoverable.
Changing burned-in captions still requires an explicit re-render or NLE export.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import uuid
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


def _ass_to_srt(value: str) -> str:
    """Convert common ASS dialogue cues to SRT without losing comma-containing text."""
    rows: list[tuple[str, str, str]] = []

    def clean_time(value: str) -> str:
        match = re.fullmatch(r"\s*(\d+):([0-5]\d):([0-5]\d)[.](\d{1,3})\s*", value)
        if not match:
            raise ValueError(f"invalid ASS timestamp: {value!r}")
        hours, minutes, seconds, fraction = match.groups()
        milliseconds = int(fraction.ljust(3, "0")[:3])
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d},{milliseconds:03d}"

    for line in value.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        fields = line.split(",", 9)
        if len(fields) < 10:
            raise ValueError("Invalid ASS dialogue row: expected ten fields")
        try:
            start = clean_time(fields[1])
            end = clean_time(fields[2])
        except ValueError as exc:
            raise ValueError("Invalid ASS dialogue timestamp") from exc
        text = re.sub(r"\{[^}]*\}", "", fields[9]).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ").strip()
        if not text:
            raise ValueError("ASS dialogue cue has empty text")
        if _seconds(end) <= _seconds(start):
            raise ValueError("ASS dialogue cue end must be after its start")
        rows.append((start, end, text))
    rows.sort(key=lambda row: _seconds(row[0]))
    return validate_srt(
        "\n\n".join(
            f"{index}\n{start} --> {end}\n{text}"
            for index, (start, end, text) in enumerate(rows, start=1)
        ) + ("\n" if rows else "")
    )


def _find_artifact(package: Path, names: tuple[str, ...]) -> Path | None:
    excluded = {"reviewed", "review-revisions", ".review-revisions"}
    root = package.resolve()
    candidates = []
    for path in package.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.name.lower() not in names:
            continue
        if set(path.relative_to(package).parts) & excluded:
            continue
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or path.stat().st_size > 2_000_000:
                continue
        except (OSError, RuntimeError):
            continue
        candidates.append(path)
    if not candidates:
        return None
    def priority(path: Path) -> tuple[int, int, str]:
        rel = path.relative_to(package).as_posix().lower()
        exact = 0 if "/" not in rel else 1
        preference = names.index(path.name.lower())
        # Prefer authoritative caption formats over a root script-only SRT;
        # within the same format prefer a package-root copy.
        return (preference, exact, rel)
    return sorted(candidates, key=priority)[0]


def _safe_read(path: Path | None) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path is not None else ""


def _review_dir(package: Path) -> Path:
    root = package.resolve()
    candidate = root / "reviewed"
    if candidate.is_symlink():
        raise ValueError("Review directory must not be a symbolic link")
    review = candidate.resolve()
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
    stored_digest = str(state.get("sha256") or "")
    if not stored_digest or not hmac.compare_digest(stored_digest, _digest(state)):
        raise ValueError("Saved review state failed its integrity check; original package files were left untouched")
    return state


def load_review(package: Path) -> dict[str, Any]:
    """Load the most recent review state or seed the workbench from package files."""
    state = _load_state(package)
    if state is not None:
        return state
    captions = _find_artifact(package, ("captions.srt", "subtitle.srt", "captions.ass", "script.srt"))
    title = _find_artifact(package, ("title.txt",))
    description = _find_artifact(package, ("description.txt",))
    package_root = package.resolve()
    video_candidates = []
    for path in package.rglob("*.mp4"):
        if path.is_symlink() or not path.is_file() or "reviewed" in path.relative_to(package).parts:
            continue
        try:
            if not path.resolve(strict=True).is_relative_to(package_root):
                continue
            if path.stat().st_size > 20 * 1024 * 1024 * 1024:
                continue
        except (OSError, RuntimeError):
            continue
        video_candidates.append(path)
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
        "captions_srt": (
            _ass_to_srt(_safe_read(captions))
            if captions is not None and captions.suffix.lower() == ".ass"
            else _safe_read(captions)
        ),
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
    captions_changed = captions != current.get("captions_srt", "")
    render_metadata: dict[str, Any] = {}
    existing_render = str(current.get("rendered_video_file") or "")
    expected_render_sha = str(current.get("rendered_video_sha256") or "")
    if not captions_changed and existing_render and expected_render_sha:
        try:
            root = package.resolve()
            candidate = (root / existing_render).resolve(strict=True)
            expected_parent = (root / "reviewed" / "renders").resolve()
            if (
                candidate.is_file()
                and candidate.is_relative_to(expected_parent)
                and not (root / existing_render).is_symlink()
                and hmac.compare_digest(_sha256_file(candidate), expected_render_sha)
            ):
                render_metadata = {
                    "rendered_for_version": version,
                    "rendered_at": str(current.get("rendered_at") or updated_at),
                    "rendered_video_file": existing_render,
                    "rendered_video_sha256": expected_render_sha,
                }
        except (OSError, RuntimeError, ValueError):
            # Do not surface a missing, altered, or escaped output as valid.
            render_metadata = {}

    if captions_changed:
        render_status = "requires_rerender"
    elif render_metadata:
        render_status = "rendered"
    else:
        render_status = "metadata_only_or_no_video_change"

    edits_payload = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "updated_at": updated_at,
        "title": title.strip(),
        "description": description.strip(),
        "captions_srt": captions,
        "source_caption_file": str(current.get("source_caption_file") or ""),
        "video_file": str(current.get("video_file") or ""),
        "render_status": render_status,
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
    state = {**edits_payload, **render_metadata, "history": history, "sha256": digest}
    _atomic_text(review_dir / "captions.srt", captions)
    _atomic_text(review_dir / "title.txt", title.strip() + ("\n" if title.strip() else ""))
    _atomic_text(review_dir / "description.txt", description.strip() + ("\n" if description.strip() else ""))
    _atomic_json(
        review_dir / "metadata.json",
        {
            "schema_version": SCHEMA_VERSION,
            "version": version,
            "updated_at": updated_at,
            "title": title.strip(),
            "description": description.strip(),
            "caption_cues": len([block for block in captions.split("\n\n") if block.strip()]),
            "render_status": edits_payload["render_status"],
            "rendered_video_file": render_metadata.get("rendered_video_file"),
            "rendered_video_sha256": render_metadata.get("rendered_video_sha256"),
            "sha256": digest,
        },
    )

    # Publish the new review state last: if any sidecar write fails (for
    # example, disk-full), readers continue to see the previous valid state.
    _atomic_json(_state_path(package), state)

    keep_versions = {int(item.get("version", 0)) for item in history}
    for path in revisions_dir.glob("rev-*.json"):
        match = _VERSION_FILE.fullmatch(path.name)
        if match and int(match.group(1)) not in keep_versions:
            try:
                path.unlink()
            except OSError:
                pass
    return state




def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_reviewed_video(package: Path) -> dict[str, Any]:
    """Re-render a reviewed V3 package into a new output without altering its source.

    Requires a valid V3 plan/timeline, an unchanged source matching
    source_manifest.json, and the original package voice.mp3. A private scratch
    workspace ensures the regular final MP4, plan, captions, and voiceover are
    never mutated by the reviewed render.
    """
    root = package.resolve()
    if not root.is_dir():
        raise ValueError("Package directory does not exist")
    state = load_review(root)
    if not str(state.get("captions_srt") or "").strip():
        raise ValueError("Add or correct at least one subtitle cue before rendering")
    for filename in ("plan.json", "timeline.json", "source_manifest.json", "voice.mp3"):
        path = root / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Reviewed rendering requires the original {filename} artifact")
    plan_path = root / "plan.json"
    timeline_path = root / "timeline.json"
    manifest_path = root / "source_manifest.json"
    for path in (plan_path, timeline_path, manifest_path):
        try:
            if path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError(f"Required artifact is too large to review-render: {path.name}")
        except OSError as exc:
            raise ValueError(f"Unable to inspect required artifact {path.name}") from exc

    try:
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("The saved V3 plan or source manifest is unreadable") from exc
    directives = plan_payload.get("v3_directives") if isinstance(plan_payload, dict) else None
    edit_plan = plan_payload.get("edit_plan") if isinstance(plan_payload, dict) else None
    if not isinstance(directives, dict) or not directives.get("blueprint_contract"):
        raise ValueError("Reviewed re-render currently requires a saved V3 blueprint contract")
    if not isinstance(edit_plan, list) or not edit_plan:
        raise ValueError("The saved V3 edit plan is missing or empty")
    if not isinstance(manifest, dict):
        raise ValueError("The saved source manifest is invalid")
    source_value = str(manifest.get("source_path") or "").strip()
    expected_sha = str(manifest.get("source_sha256") or "").strip().lower()
    if not source_value or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("The source manifest does not contain a valid original source hash")
    source = Path(source_value).expanduser()
    if source.is_symlink():
        raise ValueError("The original source is a symbolic link; reviewed rendering was refused")
    try:
        source = source.resolve(strict=True)
        if not source.is_file():
            raise ValueError("The original source is not a regular file")
        observed_sha = _sha256_file(source)
    except (OSError, RuntimeError) as exc:
        raise ValueError("The original source file is no longer available") from exc
    if not hmac.compare_digest(expected_sha, observed_sha):
        raise ValueError("The original source has changed since this package was rendered; re-render refused")

    voiceover = root / "voice.mp3"
    if voiceover.stat().st_size <= 0:
        raise ValueError("The original package voiceover is empty; reviewed re-render refused")
    caption_sidecar = _review_dir(root) / "captions.srt"
    if not caption_sidecar.is_file() or caption_sidecar.is_symlink():
        raise ValueError("Reviewed caption sidecar is missing")
    try:
        captions = validate_srt(caption_sidecar.read_text(encoding="utf-8"))
        state_captions = validate_srt(str(state.get("captions_srt") or ""))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Reviewed captions failed validation: {exc}") from exc
    if not captions:
        raise ValueError("Reviewed captions are empty")
    if captions != state_captions:
        raise ValueError("Reviewed caption sidecar does not match saved review state; save corrections again before rendering")

    review_dir = _review_dir(root)
    renders_dir = review_dir / "renders"
    if renders_dir.is_symlink():
        raise ValueError("Reviewed renders directory must not be a symbolic link")
    renders_dir.mkdir(parents=True, exist_ok=True)
    review_root = review_dir.resolve(strict=True)
    renders_root = renders_dir.resolve(strict=True)
    if not renders_root.is_relative_to(review_root):
        raise ValueError("Reviewed renders directory must remain inside the review directory")
    renders_dir = renders_root
    workspace = Path(tempfile.mkdtemp(prefix=".render-work-", dir=str(renders_dir)))
    try:
        shutil.copy2(plan_path, workspace / "plan.json")
        shutil.copy2(timeline_path, workspace / "timeline.json")
        shutil.copy2(voiceover, workspace / "voice.mp3")
        effectiveness = root / "filter_effectiveness.json"
        if effectiveness.is_file() and not effectiveness.is_symlink() and effectiveness.stat().st_size <= 2 * 1024 * 1024:
            shutil.copy2(effectiveness, workspace / "filter_effectiveness.json")
        _atomic_text(workspace / "script.srt", captions)
        # There is intentionally no captions.ass in this isolated workspace:
        # the reviewed SRT is the sole caption source for this re-render.

        from .composer import compose_short_from_video
        from .render_engine import validate_media_output

        output = workspace / "final.reviewed.mp4"
        rendered = compose_short_from_video(
            str(source),
            str(workspace),
            out_file=str(output),
            review=False,
            auto_fix=False,
            model_key=None,
            skip_qc=True,
            strict_voiceover=True,
            reuse_existing_voiceover=True,
            reuse_existing_subtitles=True,
        )
        rendered_path = Path(rendered).resolve(strict=True)
        if rendered_path != output.resolve() or not rendered_path.is_file():
            raise RuntimeError("Reviewed renderer returned an unexpected output path")
        validate_media_output(str(rendered_path), require_video=True, require_audio=True)
        if rendered_path.stat().st_size <= 0:
            raise RuntimeError("Reviewed render is empty")
        version = int(state.get("version", 0))
        stamp = uuid.uuid4().hex[:8]
        filename = f"final.reviewed-v{version}-{stamp}.mp4"
        destination = renders_dir / filename
        os.replace(rendered_path, destination)
        relative = destination.relative_to(root).as_posix()
        rendered_sha = _sha256_file(destination)
        updated = dict(state)
        updated.update({
            "render_status": "rendered",
            "rendered_for_version": version,
            "rendered_at": _now(),
            "rendered_video_file": relative,
            "rendered_video_sha256": rendered_sha,
        })
        # The integrity digest intentionally covers editable review content only;
        # render bookkeeping cannot invalidate the saved review's own hash.
        _atomic_json(_state_path(root), updated)
        return updated
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


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
    history_entry = next(
        (item for item in current.get("history", []) if int(item.get("version", 0)) == int(version)),
        None,
    )
    expected_digest = str(history_entry.get("sha256") or "") if isinstance(history_entry, dict) else ""
    if (
        not isinstance(previous, dict)
        or not expected_digest
        or not hmac.compare_digest(expected_digest, _digest(previous))
    ):
        raise ValueError("Requested review revision failed its integrity check")
    return save_review(package, previous, restored_from=int(version))


__all__ = ["load_review", "render_reviewed_video", "restore_review", "save_review", "validate_srt"]
