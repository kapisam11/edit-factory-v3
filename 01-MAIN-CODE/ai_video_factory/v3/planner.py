"""Modular V3 planning facade over the compatibility engine."""
from __future__ import annotations
from typing import Any

class V3Planner:
    def plan(self, topic: str, *, context: str = "", config: Any = None, edit_type: str | None = None) -> Any:
        from ..v3_engine import create_v3_blueprint
        return create_v3_blueprint(topic, context=context, config=config, edit_type=edit_type)

__all__ = ["V3Planner"]
