"""Semantic editorial effects IR compiled separately from FFmpeg."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class EffectKind(str, Enum):
    CUT = "cut"
    ZOOM = "zoom"
    CROP = "crop"
    CAPTION = "caption"
    TRANSITION = "transition"
    MUSIC_CHANGE = "music_change"
    DO_NOTHING = "do_nothing"


@dataclass(frozen=True)
class Effect:
    kind: EffectKind
    start: float
    duration: float
    parameters: Mapping[str, float | str] = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "start": round(self.start, 3),
            "duration": round(self.duration, 3),
            "parameters": dict(self.parameters),
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class RenderIR:
    video_segments: tuple[Mapping[str, Any], ...] = ()
    overlays: tuple[Mapping[str, Any], ...] = ()
    effects: tuple[Effect, ...] = ()
    audio: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "1.0.0",
            "video_segments": [dict(item) for item in self.video_segments],
            "overlays": [dict(item) for item in self.overlays],
            "effects": [item.to_dict() for item in self.effects],
            "audio": [dict(item) for item in self.audio],
        }


class EffectCompiler:
    """Translate semantic editorial events into renderer-neutral effects."""

    _MAP = {
        "zoom": EffectKind.ZOOM,
        "motion": EffectKind.ZOOM,
        "angle": EffectKind.CROP,
        "text": EffectKind.CAPTION,
        "clip": EffectKind.CUT,
        "beat drop": EffectKind.TRANSITION,
    }

    def compile_retention(self, event: Mapping[str, Any]) -> Effect:
        timestamp = float(event.get("time", 0.0))
        kind = str(event.get("kind", "do_nothing")).strip().lower()
        confidence = float(event.get("confidence", 0.0))
        reason = str(event.get("reason", "")).strip()
        if kind == "do_nothing" or confidence < 0.60:
            return Effect(EffectKind.DO_NOTHING, timestamp, 0.0, reason=reason, confidence=confidence)
        effect_kind = self._MAP.get(kind, EffectKind.DO_NOTHING)
        if effect_kind is EffectKind.DO_NOTHING:
            return Effect(EffectKind.DO_NOTHING, timestamp, 0.0, reason="UNSUPPORTED_EFFECT", confidence=0.0)
        parameters: dict[str, float | str] = {}
        if effect_kind is EffectKind.ZOOM:
            parameters = {"scale_start": 1.00, "scale_end": 1.08}
        elif effect_kind is EffectKind.CROP:
            parameters = {"reframe_fraction": 0.08}
        elif effect_kind is EffectKind.CAPTION:
            parameters = {"readability": "high"}
        elif effect_kind is EffectKind.TRANSITION:
            parameters = {"style": "restrained"}
        return Effect(effect_kind, timestamp, 0.22, parameters=parameters, reason=reason, confidence=confidence)

    def compile(self, events: Sequence[Mapping[str, Any]]) -> RenderIR:
        return RenderIR(effects=tuple(self.compile_retention(item) for item in events if isinstance(item, Mapping)))


__all__ = ["Effect", "EffectCompiler", "EffectKind", "RenderIR"]
