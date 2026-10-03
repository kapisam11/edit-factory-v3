"""Stage-specific validation boundaries."""
from __future__ import annotations
from typing import Any,Mapping

def validate_input(request: Any) -> None:
    request.validate()

def validate_plan(blueprint: Any) -> None:
    from ..v3_engine import validate_blueprint
    validate_blueprint(blueprint)

def validate_render(path: str, *, target_seconds: float, platform_profile: Mapping[str, Any], retention_events: tuple[Mapping[str, Any], ...] = ()):
    from ..v3_quality import strict_render_check
    return strict_render_check(path,target_seconds=target_seconds,platform_profile=platform_profile,retention_events=retention_events)

def validate_release(readiness: Any) -> None:
    if getattr(readiness,"errors",None):
        raise ValueError("release validation failed")

__all__=["validate_input","validate_plan","validate_render","validate_release"]
