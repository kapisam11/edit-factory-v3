"""Strict parsers for structured AI responses entering production state."""
from __future__ import annotations

import json
from typing import Any, Optional

def parse_script_lines_response(response: str, *, min_lines: int = 2, max_lines: int = 40) -> Optional[list[str]]:
    """Parse only a JSON object containing a bounded list of non-empty strings."""
    text = str(response or "").strip()
    if not text or len(text) > 20_000:
        return None
    if text.startswith("```"):
        parts = text.splitlines()
        if parts and parts[0].strip().startswith("```"):
            parts = parts[1:]
        if parts and parts[-1].strip() == "```":
            parts = parts[:-1]
        text = "\n".join(parts).strip()
    try:
        payload: Any = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or set(payload) - {"lines"} or "lines" not in payload:
        return None
    lines = payload["lines"]
    if not isinstance(lines, list) or not min_lines <= len(lines) <= max_lines:
        return None
    if any(not isinstance(line, str) or not line.strip() or len(line) > 300 for line in lines):
        return None
    return [line.strip() for line in lines]

__all__ = ["parse_script_lines_response"]
