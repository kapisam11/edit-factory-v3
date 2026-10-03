"""Semantic editorial effects IR and a renderer-neutral FFmpeg graph compiler."""
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
            "version": "1.2.0",
            "video_segments": [dict(item) for item in self.video_segments],
            "overlays": [dict(item) for item in self.overlays],
            "effects": [item.to_dict() for item in self.effects],
            "audio": [dict(item) for item in self.audio],
        }


@dataclass(frozen=True)
class CompiledRenderGraph:
    video_filters: tuple[str, ...] = ()
    audio_filters: tuple[str, ...] = ()
    passthrough_effects: tuple[Effect, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_filters": list(self.video_filters),
            "audio_filters": list(self.audio_filters),
            "passthrough_effects": [item.to_dict() for item in self.passthrough_effects],
        }


def _escape_drawtext(text: str) -> str:
    return (
        str(text or "Key detail")
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
        .replace(",", "\\,")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


class EffectCompiler:
    """Translate semantic editorial events into renderer-neutral effects and FFmpeg graph fragments."""

    _MAP = {
        "zoom": EffectKind.ZOOM,
        "motion": EffectKind.ZOOM,
        "angle": EffectKind.CROP,
        "text": EffectKind.CAPTION,
        "beat drop": EffectKind.TRANSITION,
    }

    def compile_retention(self, event: Mapping[str, Any]) -> Effect:
        timestamp = float(event.get("time", 0.0))
        kind = str(event.get("kind", "do_nothing")).strip().lower()
        confidence = float(event.get("confidence", 0.0))
        reason = str(event.get("reason", "")).strip()
        if kind == "clip":
            return Effect(
                EffectKind.DO_NOTHING,
                timestamp,
                0.0,
                reason="STRUCTURAL_CUT_HANDLED_BY_TIMELINE",
                confidence=confidence,
            )
        if kind == "do_nothing" or confidence < 0.60:
            return Effect(
                EffectKind.DO_NOTHING,
                timestamp,
                0.0,
                reason=reason,
                confidence=confidence,
            )
        effect_kind = self._MAP.get(kind, EffectKind.DO_NOTHING)
        if effect_kind is EffectKind.DO_NOTHING:
            return Effect(
                EffectKind.DO_NOTHING,
                timestamp,
                0.0,
                reason="UNSUPPORTED_EFFECT",
                confidence=0.0,
            )

        parameters: dict[str, float | str] = {}
        if effect_kind is EffectKind.ZOOM:
            parameters = {"scale": 1.08, "implementation": "crop_then_scale"}
        elif effect_kind is EffectKind.CROP:
            parameters = {"reframe_fraction": 0.08, "implementation": "crop_then_scale"}
        elif effect_kind is EffectKind.CAPTION:
            parameters = {
                "text": str(event.get("text") or event.get("caption") or reason.replace("_", " ").title())[:48],
                "implementation": "drawtext",
            }
        elif effect_kind is EffectKind.TRANSITION:
            parameters = {"implementation": "fade_flash", "color": "black"}

        return Effect(
            effect_kind,
            timestamp,
            0.22,
            parameters=parameters,
            reason=reason,
            confidence=confidence,
        )

    def compile(self, events: Sequence[Mapping[str, Any]]) -> RenderIR:
        return RenderIR(
            effects=tuple(
                self.compile_retention(item)
                for item in events
                if isinstance(item, Mapping)
            )
        )

    def compile_ffmpeg_graph(
        self,
        render_ir: RenderIR,
        *,
        output_width: int = 1080,
        output_height: int = 1920,
    ) -> CompiledRenderGraph:
        if output_width <= 0 or output_height <= 0:
            raise ValueError("output dimensions must be positive")

        filters: list[str] = []
        passthrough: list[Effect] = []
        for effect in render_ir.effects:
            if effect.kind is EffectKind.DO_NOTHING:
                passthrough.append(effect)
                continue

            start = max(0.0, effect.start - 0.04)
            duration = max(0.08, effect.duration)
            end = start + duration

            if effect.kind is EffectKind.ZOOM:
                filters.append(
                    "crop=w='floor(iw/1.08/2)*2':"
                    "h='floor(ih/1.08/2)*2':"
                    "x='(iw-ow)/2':y='(ih-oh)/2':"
                    f"enable='between(t,{start:.3f},{end:.3f})',"
                    f"scale={int(output_width)}:{int(output_height)}:flags=lanczos"
                )
            elif effect.kind is EffectKind.CROP:
                filters.append(
                    "crop=w='floor(iw/1.08/2)*2':"
                    "h='floor(ih/1.08/2)*2':"
                    "x='(iw-ow)*0.62':y='(ih-oh)*0.38':"
                    f"enable='between(t,{start:.3f},{end:.3f})',"
                    f"scale={int(output_width)}:{int(output_height)}:flags=lanczos"
                )
            elif effect.kind is EffectKind.CAPTION:
                text = _escape_drawtext(str(effect.parameters.get("text") or "Key detail"))
                filters.append(
                    f"drawtext=text='{text}':x=(w-text_w)/2:y=h-text_h-80:"
                    f"fontsize=48:fontcolor=white:borderw=3:bordercolor=black:"
                    f"enable='between(t,{start:.3f},{end:.3f})'"
                )
            elif effect.kind is EffectKind.TRANSITION:
                filters.append(
                    f"fade=t=out:st={start:.3f}:d={duration / 2:.3f}:color=black,"
                    f"fade=t=in:st={start + duration / 2:.3f}:d={duration / 2:.3f}:color=black"
                )

        return CompiledRenderGraph(tuple(filters), (), tuple(passthrough))


__all__ = [
    "CompiledRenderGraph",
    "Effect",
    "EffectCompiler",
    "EffectKind",
    "RenderIR",
]
