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
    video_filter_complex: str | None = None
    video_output_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_filters": list(self.video_filters),
            "audio_filters": list(self.audio_filters),
            "passthrough_effects": [item.to_dict() for item in self.passthrough_effects],
            "video_filter_complex": self.video_filter_complex,
            "video_output_label": self.video_output_label,
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
        input_width: int | None = None,
        input_height: int | None = None,
    ) -> CompiledRenderGraph:
        if output_width <= 0 or output_height <= 0:
            raise ValueError("output dimensions must be positive")
        source_width = int(input_width or output_width)
        source_height = int(input_height or output_height)
        if source_width <= 0 or source_height <= 0:
            raise ValueError("input dimensions must be positive")

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
                gate = f"between(t,{start:.3f},{end:.3f})"
                zoom_w = f"if({gate},ceil(iw*1.08/2)*2,iw)"
                zoom_h = f"if({gate},ceil(ih*1.08/2)*2,ih)"
                filters.append(
                    f"scale=w='{zoom_w}':h='{zoom_h}':eval=frame:flags=lanczos,"
                    f"crop={source_width}:{source_height}:x=(iw-ow)/2:y=(ih-oh)/2"
                )
            elif effect.kind is EffectKind.CROP:
                gate = f"between(t,{start:.3f},{end:.3f})"
                crop_x = f"if({gate},(iw-ow)*0.62,0)"
                crop_y = f"if({gate},(ih-oh)*0.38,0)"
                crop_w = f"if({gate},iw,{source_width})"
                crop_h = f"if({gate},ih,{source_height})"
                filters.append(
                    f"scale=w='{crop_w}':h='{crop_h}':eval=frame:flags=lanczos,"
                    f"crop={source_width}:{source_height}:x='{crop_x}':y='{crop_y}'"
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


def _compile_ffmpeg_filter_complex(
    self: EffectCompiler,
    render_ir: RenderIR,
    *,
    output_width: int = 1080,
    output_height: int = 1920,
    input_width: int | None = None,
    input_height: int | None = None,
) -> CompiledRenderGraph:
    """Compile effect windows over an untouched base stream."""
    if output_width <= 0 or output_height <= 0:
        raise ValueError("output dimensions must be positive")
    source_width = int(input_width or output_width)
    source_height = int(input_height or output_height)
    if source_width <= 0 or source_height <= 0:
        raise ValueError("input dimensions must be positive")

    effectful = [
        effect
        for effect in render_ir.effects
        if effect.kind is not EffectKind.DO_NOTHING
    ]
    passthrough = tuple(
        effect
        for effect in render_ir.effects
        if effect.kind is EffectKind.DO_NOTHING
    )
    if not effectful:
        return CompiledRenderGraph(passthrough_effects=passthrough)

    parts: list[str] = []
    current = "v0"
    if source_width != output_width or source_height != output_height:
        parts.append(
            "[0:v]scale=%d:%d:flags=lanczos[%s]"
            % (int(output_width), int(output_height), current)
        )
        source_width = int(output_width)
        source_height = int(output_height)
    else:
        parts.append("[0:v]null[%s]" % current)

    for index, effect in enumerate(effectful, start=1):
        start = max(0.0, effect.start - 0.04)
        duration = max(0.08, effect.duration)
        end = start + duration
        base = "base%d" % index
        fx = "fx%d" % index
        effected = "effect%d" % index
        next_label = "v%d" % index
        parts.append("[%s]split=2[%s][%s]" % (current, base, fx))

        if effect.kind is EffectKind.ZOOM:
            parts.append(
                "[%s]scale=ceil(iw*1.08/2)*2:ceil(ih*1.08/2)*2:eval=frame:flags=lanczos,"
                "crop=%d:%d:x=(iw-ow)/2:y=(ih-oh)/2[%s]"
                % (fx, source_width, source_height, effected)
            )
        elif effect.kind is EffectKind.CROP:
            crop_width = max(2, int(source_width * 0.925))
            crop_height = max(2, int(source_height * 0.925))
            crop_width -= crop_width % 2
            crop_height -= crop_height % 2
            parts.append(
                "[%s]crop=%d:%d:x=(iw-ow)*0.62:y=(ih-oh)*0.38,"
                "scale=%d:%d:flags=lanczos[%s]"
                % (
                    fx,
                    crop_width,
                    crop_height,
                    int(output_width),
                    int(output_height),
                    effected,
                )
            )
        elif effect.kind is EffectKind.CAPTION:
            text = _escape_drawtext(str(effect.parameters.get("text") or "Key detail"))
            parts.append(
                "[%s]drawtext=text='%s':x=(w-text_w)/2:y=h-text_h-80:"
                "fontsize=48:fontcolor=white:borderw=3:bordercolor=black:"
                "enable='between(t,%.3f,%.3f)'[%s]"
                % (fx, text, start, end, effected)
            )
        elif effect.kind is EffectKind.TRANSITION:
            midpoint = start + duration / 2.0
            parts.append(
                "[%s]fade=t=out:st=%.3f:d=%.3f:color=black,"
                "fade=t=in:st=%.3f:d=%.3f:color=black[%s]"
                % (fx, start, duration / 2.0, midpoint, duration / 2.0, effected)
            )
        else:
            parts.append("[%s]null[%s]" % (fx, effected))

        enable = "between(t,%.3f,%.3f)" % (start, end)
        parts.append(
            "[%s][%s]overlay=0:0:enable='%s':eof_action=pass:shortest=1[%s]"
            % (base, effected, enable, next_label)
        )
        current = next_label

    return CompiledRenderGraph(
        passthrough_effects=passthrough,
        video_filter_complex=";".join(parts),
        video_output_label=current,
    )


EffectCompiler.compile_ffmpeg_filter_complex = _compile_ffmpeg_filter_complex


__all__ = [
    "CompiledRenderGraph",
    "Effect",
    "EffectCompiler",
    "EffectKind",
    "RenderIR",
]
