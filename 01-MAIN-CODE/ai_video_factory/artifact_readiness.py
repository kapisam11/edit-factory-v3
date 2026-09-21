"""Evidence-backed artifact readiness states for production output.

The state is the highest contract that has actually been verified. Callers must
not infer PUBLISH_READY from MEDIA_VALID alone.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Any, Mapping, Optional
from .v3_quality import probe_media


FINAL_VIDEO_CANDIDATES = ("final.v3.mp4", "final_with_music.mp4", "final_short.mp4", "final_short_vo.mp4", "final.mp4")

READINESS_STATES = (
    "MEDIA_VALID",
    "MEDIA_CONTRACT_VALID",
    "UPLOAD_PACKAGE_VALID",
    "PUBLISH_READY",
)


@dataclass(frozen=True)
class ReadinessReport:
    state: str
    checks: dict[str, bool]
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_final_video(package_dir: str | Path) -> Optional[Path]:
    """Return the first valid completed video artifact for a package.

    V3 packages are canonical-only: once ``v3_blueprint.json`` exists, a
    legacy filename must never mask a missing or corrupt ``final.v3.mp4``.
    Every candidate is probed before it can be returned.
    """
    root = Path(package_dir).resolve()
    if not root.is_dir():
        return None

    candidates = ("final.v3.mp4",) if (root / "v3_blueprint.json").is_file() else FINAL_VIDEO_CANDIDATES
    for name in candidates:
        candidate = (root / name).resolve()
        if root not in candidate.parents or not candidate.is_file():
            continue
        try:
            probe_media(str(candidate))
        except Exception:
            continue
        return candidate
    return None

def _highest_state(checks: Mapping[str, bool]) -> str:
    state = "MEDIA_INVALID"
    for candidate in READINESS_STATES:
        if not checks.get(candidate):
            break
        state = candidate
    return state


def evaluate_artifact(
    final_video: str,
    *,
    target_seconds: Optional[float] = None,
    platform_profile: Optional[Mapping[str, Any]] = None,
    package_dir: Optional[str] = None,
    upload_package_required: bool = False,
    publish_required: bool = False,
    publish_prerequisites_met: bool = False,
) -> ReadinessReport:
    errors: list[str] = []
    warnings: list[str] = []
    checks = {name: False for name in READINESS_STATES}

    try:
        info = probe_media(final_video)
        checks["MEDIA_VALID"] = True
    except Exception as exc:
        errors.append(f"MEDIA_VALID: {exc}")
        return ReadinessReport(_highest_state(checks), checks, errors, warnings)

    if target_seconds is None:
        warnings.append("MEDIA_CONTRACT_VALID was not evaluated because no target duration was supplied")
    else:
        duration_ok = abs(float(info["duration"]) - float(target_seconds)) <= 0.08
        width_ok = height_ok = True
        if platform_profile:
            width_ok = not platform_profile.get("width") or info["width"] == int(platform_profile["width"])
            height_ok = not platform_profile.get("height") or info["height"] == int(platform_profile["height"])
        checks["MEDIA_CONTRACT_VALID"] = duration_ok and width_ok and height_ok
        if not duration_ok:
            errors.append("MEDIA_CONTRACT_VALID: duration is outside the allowed tolerance")
        if not width_ok or not height_ok:
            errors.append("MEDIA_CONTRACT_VALID: dimensions do not match the platform contract")

    package = Path(package_dir).resolve() if package_dir else None
    upload_manifest = package / "upload_package.json" if package else None
    upload_ok = False
    if upload_manifest and upload_manifest.is_file():
        try:
            manifest = json.loads(upload_manifest.read_text(encoding="utf-8"))
            platforms = manifest.get("platforms") if isinstance(manifest, dict) else None
            upload_ok = isinstance(platforms, dict) and bool(platforms)
            if upload_ok:
                for platform, payload in platforms.items():
                    if not isinstance(payload, dict):
                        upload_ok = False
                        errors.append(f"UPLOAD_PACKAGE_VALID: invalid manifest entry for {platform}")
                        break
                    files = payload.get("files") or {}
                    video_rel = files.get("video") if isinstance(files, dict) else None
                    if not isinstance(video_rel, str):
                        upload_ok = False
                        errors.append(f"UPLOAD_PACKAGE_VALID: platform {platform} has no video file")
                        break
                    target = (package / video_rel).resolve()
                    if package not in target.parents or not target.is_file():
                        upload_ok = False
                        errors.append(f"UPLOAD_PACKAGE_VALID: platform {platform} video is missing")
                        break
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append(f"UPLOAD_PACKAGE_VALID: invalid upload package manifest: {exc}")
    elif package and package.exists() and not upload_package_required:
        warnings.append("No upload manifest is present; upload-package readiness is not claimed")
    checks["UPLOAD_PACKAGE_VALID"] = upload_ok
    if upload_package_required and not upload_ok:
        errors.append("UPLOAD_PACKAGE_VALID: required upload package is incomplete")

    checks["PUBLISH_READY"] = (
        checks["MEDIA_CONTRACT_VALID"]
        and checks["UPLOAD_PACKAGE_VALID"]
        and (publish_prerequisites_met or not publish_required)
        and not errors
    )
    if not publish_required:
        checks["PUBLISH_READY"] = False
    elif not publish_prerequisites_met:
        errors.append("PUBLISH_READY: platform publish prerequisites have not been explicitly satisfied")
    elif not checks["PUBLISH_READY"]:
        errors.append("PUBLISH_READY: one or more required release gates are not satisfied")

    return ReadinessReport(_highest_state(checks), checks, errors, warnings)


__all__ = ["FINAL_VIDEO_CANDIDATES", "READINESS_STATES", "ReadinessReport", "evaluate_artifact", "resolve_final_video"]
