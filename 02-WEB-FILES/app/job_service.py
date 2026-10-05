"""Framework-independent job parameter validation for the dashboard."""
from __future__ import annotations

import os
from typing import Any, Mapping

from ai_video_factory.v3_engine import EditType, V3Config
from ai_video_factory.validation import normalize_workflow, validate_target_seconds, validate_v3_target_seconds


def _flag(value: Any) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def build_job_params(data: Mapping[str, Any], settings: Mapping[str, Any], *, allow_skip_qc: bool) -> dict[str, Any]:
    topic = str(data.get("topic", "")).strip()
    if not 2 <= len(topic) <= 500:
        raise ValueError("Topic must be between 2 and 500 characters")
    workflow = normalize_workflow(data.get("workflow", settings["default_workflow"]))
    raw_target = data.get("target_seconds", settings.get("default_target_seconds", 45.0))
    target_seconds = validate_v3_target_seconds(raw_target) if workflow == "v3" else validate_target_seconds(raw_target)
    params: dict[str, Any] = {
        "topic": topic,
        "target_seconds": target_seconds,
        "workflow": workflow,
        "use_groq": _flag(data.get("use_groq")),
        "skip_qc": bool(allow_skip_qc and _flag(data.get("skip_qc"))),
    }
    if workflow != "v3":
        return params

    platform = str(data.get("platform", settings.get("default_v3_platform", "youtube_shorts"))).strip().lower()
    audience = str(data.get("audience", settings.get("default_v3_audience", "general short-form viewers"))).strip()
    edit_type = str(data.get("edit_type", "")).strip() or None
    try:
        bpm = int(data.get("bpm", settings.get("default_v3_bpm", 120)))
    except (TypeError, ValueError) as exc:
        raise ValueError("bpm must be an integer between 40 and 240") from exc
    if edit_type and edit_type not in {item.value for item in EditType}:
        raise ValueError("Unsupported V3 edit type")
    try:
        V3Config(target_seconds=float(target_seconds), platform=platform, audience=audience, bpm=bpm).validate()
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if not audience or len(audience) > 500:
        raise ValueError("V3 audience must be between 1 and 500 characters")
    params.update({
        "platform": platform,
        "audience": audience,
        "bpm": bpm,
        "edit_type": edit_type,
        "context": str(data.get("context", "")).strip()[:2000],
        "enable_ocr": _flag(data.get("enable_ocr")),
        "enable_object_detection": _flag(data.get("enable_object_detection", "false")),
        "enable_diarization": _flag(data.get("enable_diarization")),
        "allow_unsupported_critical_evidence": bool(
            os.environ.get("AIVF_ENV", "").strip().lower() == "test"
            and _flag(data.get("allow_unsupported_critical_evidence"))
        ),
        "source_metadata": {
            "creator": str(data.get("source_creator", "")).strip()[:200],
            "title": str(data.get("source_title", "")).strip()[:300],
            "url": str(data.get("source_url", "")).strip()[:1000],
            "rights_basis": str(data.get("rights_basis", "")).strip().lower(),
            # Preserve the caller's explicit declaration for the downstream
            # evidence gate. The gate still requires valid evidence/identity/time.
            "rights_status": str(data.get("rights_status") or data.get("rights_basis", "")).strip().lower(),
            "evidence_url": str(data.get("rights_evidence_url", "")).strip()[:1000],
            "license_url": str(data.get("license_url", "")).strip()[:1000],
            "source": "user_provided",
        },
    })
    return params


__all__ = ["build_job_params"]
