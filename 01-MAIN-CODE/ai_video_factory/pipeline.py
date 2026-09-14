"""AI Video Factory pipeline stage system.

Discrete, testable, swappable pipeline stages. Each stage receives a
PipelineContext and returns a modified context.
"""
import json
import logging
import os
import shutil
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .validation import validate_target_seconds

logger = logging.getLogger(__name__)
ProgressCallback = Callable[[int, int, str, float, float], None]


class Severity(Enum):
    CRITICAL = "critical"
    DEGRADED = "degraded"
    OPTIONAL = "optional"


@dataclass
class StepResult:
    ok: bool
    output: Any = None
    severity: Severity = Severity.DEGRADED
    notes: Optional[List[str]] = None

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []

    @property
    def failed_critical(self) -> bool:
        return not self.ok and self.severity == Severity.CRITICAL

    @property
    def failed_degraded(self) -> bool:
        return not self.ok and self.severity == Severity.DEGRADED

    @classmethod
    def success(cls, output: Any = None, notes: Optional[List[str]] = None) -> "StepResult":
        return cls(ok=True, output=output, severity=Severity.OPTIONAL, notes=notes or [])

    @classmethod
    def critical(cls, output: Any = None, notes: Optional[List[str]] = None) -> "StepResult":
        return cls(ok=False, output=output, severity=Severity.CRITICAL, notes=notes or [])

    @classmethod
    def degraded(cls, output: Any = None, notes: Optional[List[str]] = None) -> "StepResult":
        return cls(ok=False, output=output, severity=Severity.DEGRADED, notes=notes or [])

    @classmethod
    def optional(cls, output: Any = None, notes: Optional[List[str]] = None) -> "StepResult":
        return cls(ok=False, output=output, severity=Severity.OPTIONAL, notes=notes or [])


class PipelineError(Exception):
    def __init__(self, result: StepResult, step_name: str = "") -> None:
        self.result = result
        self.step_name = step_name
        super().__init__(f"Pipeline step '{step_name}' failed: {result.notes}")


class PipelineManifest:
    def __init__(self, topic: str, target_seconds: float = 45.0) -> None:
        self.topic = topic
        self.target_seconds = target_seconds
        self.steps: Dict[str, StepResult] = {}
        self.degraded = False
        self.critical_failure: Optional[str] = None
        self.artifacts: Dict[str, Any] = {}

    def record(self, name: str, result: StepResult) -> None:
        self.steps[name] = result
        if result.failed_degraded:
            self.degraded = True
        if result.failed_critical:
            self.critical_failure = name

    def add_artifact(self, key: str, value: Any) -> None:
        self.artifacts[key] = value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "target_seconds": self.target_seconds,
            "steps": {
                name: {
                    "ok": result.ok,
                    "output": result.output,
                    "severity": result.severity.value,
                    "notes": result.notes or [],
                }
                for name, result in self.steps.items()
            },
            "degraded": self.degraded,
            "critical_failure": self.critical_failure,
            "artifacts": self.artifacts,
        }


@dataclass
class PipelineContext:
    topic: str
    package_dir: Optional[str] = None
    raw_video: Optional[str] = None
    target_seconds: float = 45.0
    skip_qc: bool = False
    use_groq: bool = False
    model_key: Optional[str] = None
    groq_key: Optional[str] = None
    thumbnail_variant: int = 1
    research: Dict[str, Any] = field(default_factory=dict)
    plan: Dict[str, Any] = field(default_factory=dict)
    script: str = ""
    edit_plan: List[Dict[str, Any]] = field(default_factory=list)
    clips: List[str] = field(default_factory=list)
    final_video: Optional[str] = None
    thumbnail: Optional[str] = None
    thumbnail_variants: List[str] = field(default_factory=list)
    voiceover: Optional[str] = None
    music_track: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    qc_report: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stage_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.target_seconds = validate_target_seconds(self.target_seconds)
        self.thumbnail_variant = int(self.thumbnail_variant)
        if self.thumbnail_variant not in {1, 2, 3}:
            raise ValueError("thumbnail_variant must be 1, 2, or 3")

    def to_json(self) -> str:
        return json.dumps(
            {k: v for k, v in self.__dict__.items() if k not in ("model_key", "groq_key")},
            indent=2,
            default=str,
        )


class PipelineStage(ABC):
    name = "stage"
    skippable = False
    retryable = True
    max_retries = 1

    @abstractmethod
    def run(self, ctx: PipelineContext) -> PipelineContext:
        ...

    @staticmethod
    def is_retryable_error(error: Exception) -> bool:
        if isinstance(error, (TimeoutError, ConnectionError)):
            return True
        if isinstance(error, sqlite3.OperationalError):
            text = str(error).lower()
            return "locked" in text or "busy" in text
        if isinstance(error, OSError) and not isinstance(error, FileNotFoundError):
            return True
        text = str(error).lower()
        return "429" in text or "temporarily unavailable" in text or "timeout" in text

    def on_error(self, ctx: PipelineContext, error: Exception) -> PipelineContext:
        ctx.errors.append(f"[{self.name}] {error}")
        ctx.stage_results[self.name] = {
            "ok": False,
            "status": "skipped" if self.skippable else "failed",
            "error": str(error),
        }
        if not self.skippable:
            raise error
        ctx.warnings.append(f"[{self.name}] Skipped due to error: {error}")
        return ctx


class Pipeline:
    def __init__(
        self,
        stages: List[PipelineStage],
        verbose: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        self.stages = stages
        self.verbose = verbose
        self.progress_callback = progress_callback
        self._stage_times: Dict[str, float] = {}

    def run(self, ctx: PipelineContext) -> PipelineContext:
        total = len(self.stages)
        completed = 0
        overall_start = time.monotonic()
        for stage in self.stages:
            start = time.monotonic()
            if self.verbose:
                logger.info("[PIPELINE] → %s", stage.name)
            attempts = 0
            success = False
            last_error: Optional[Exception] = None
            allowed_retries = stage.max_retries if stage.retryable else 0
            while attempts <= allowed_retries and not success:
                try:
                    ctx = stage.run(ctx)
                    success = True
                    ctx.stage_results[stage.name] = {
                        "ok": True,
                        "status": "completed",
                        "attempts": attempts + 1,
                    }
                except Exception as exc:
                    last_error = exc
                    attempts += 1
                    if attempts <= allowed_retries and not stage.is_retryable_error(exc):
                        logger.info("[PIPELINE] %s failed with non-retryable error: %s", stage.name, exc)
                        break
                    if attempts <= allowed_retries:
                        time.sleep(0.5 * attempts)
            if not success and last_error is not None:
                ctx = stage.on_error(ctx, last_error)
            elapsed = time.monotonic() - start
            self._stage_times[stage.name] = elapsed
            ctx.stage_results.setdefault(stage.name, {})["elapsed_seconds"] = round(elapsed, 4)
            completed += 1
            elapsed_total = time.monotonic() - overall_start
            average = elapsed_total / completed
            eta = max(0.0, average * (total - completed))
            if self.progress_callback:
                self.progress_callback(completed, total, stage.name, elapsed_total, eta)
            if self.verbose:
                status = "✓" if success else ("⚠ skipped" if stage.skippable else "✗ FAILED")
                logger.info("[PIPELINE]   %s %s (%.2fs)", status, stage.name, elapsed)
        if ctx.package_dir:
            report_path = os.path.join(ctx.package_dir, "pipeline_report.json")
            with open(report_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "stages": [stage.name for stage in self.stages],
                        "stage_times": self._stage_times,
                        "stage_results": ctx.stage_results,
                        "errors": ctx.errors,
                        "warnings": ctx.warnings,
                        "completed_at": datetime.now().isoformat(),
                    },
                    handle,
                    indent=2,
                )
        return ctx

    def get_report(self) -> Dict[str, Any]:
        return {"stages": [stage.name for stage in self.stages], "stage_times": self._stage_times}


class ResearchStage(PipelineStage):
    name = "research"
    skippable = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from .capability_registry import build_default_registry
        result = build_default_registry().call("research", query=ctx.topic)
        if result.success:
            ctx.research = result.data if isinstance(result.data, dict) else {"summary": result.data}
        else:
            ctx.warnings.append(f"Research failed: {result.error}")
            ctx.research = {"title": ctx.topic, "topic": ctx.topic}
        return ctx


class PlanStage(PipelineStage):
    name = "plan"
    skippable = False

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from .plan import make_idea
        summary = ctx.research or {"title": ctx.topic, "topic": ctx.topic}
        ctx.plan = make_idea(summary)
        ctx.edit_plan = ctx.plan.get("edit_plan", [])
        if not ctx.plan:
            raise RuntimeError("Plan generation produced no plan")
        return ctx


class ScriptStage(PipelineStage):
    name = "script"
    skippable = False

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from .story import generate_script
        ctx.script = generate_script(ctx.plan, ctx.topic)
        if not ctx.script.strip():
            raise RuntimeError("Script generation produced an empty script")
        return ctx


class ThumbnailStage(PipelineStage):
    name = "thumbnail"
    skippable = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.package_dir:
            ctx.thumbnail = None
            return ctx
        from .thumbnail import make_thumbnail_variants, make_thumbnail_vertical
        subject = str((ctx.plan.get("title_options") or [ctx.topic])[0])[:80]
        thumb_dir = os.path.join(ctx.package_dir, "thumbnails")
        ctx.thumbnail_variants = make_thumbnail_variants(subject, thumb_dir, count=3, topic=ctx.topic)
        try:
            selected = ctx.thumbnail_variants[ctx.thumbnail_variant - 1]
        except (IndexError, TypeError):
            raise RuntimeError("Thumbnail generation returned too few variants")
        ctx.thumbnail = os.path.join(ctx.package_dir, "thumbnail.png")
        shutil.copyfile(selected, ctx.thumbnail)
        vertical_path = os.path.join(ctx.package_dir, "thumbnail_vertical.png")
        make_thumbnail_vertical(subject, vertical_path, size=(1080, 1920))
        with open(os.path.join(ctx.package_dir, "thumbnail_experiment.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "variants": [os.path.relpath(path, ctx.package_dir) for path in ctx.thumbnail_variants],
                    "selected_variant": ctx.thumbnail_variant,
                    "subject": subject,
                },
                handle,
                indent=2,
            )
        return ctx


class AutoEditStage(PipelineStage):
    name = "auto_edit"
    skippable = True
    retryable = True
    max_retries = 2

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.raw_video:
            ctx.warnings.append("No raw video provided; auto-edit stage intentionally skipped")
            return ctx
        if not ctx.package_dir:
            raise RuntimeError("Auto-edit requires package_dir")
        from .composer import compose_short_from_video
        ctx.final_video = compose_short_from_video(
            ctx.raw_video,
            ctx.package_dir,
            review=not ctx.skip_qc,
            auto_fix=True,
            model_key=ctx.model_key,
            skip_qc=ctx.skip_qc,
        )
        if not ctx.final_video or not os.path.exists(ctx.final_video):
            raise RuntimeError("Auto-edit did not produce a final video")
        return ctx


class VoiceoverStage(PipelineStage):
    name = "voiceover"
    skippable = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.script or not ctx.package_dir:
            return ctx
        from .capability_registry import build_default_registry
        path = os.path.join(ctx.package_dir, "voiceover.mp3")
        result = build_default_registry().call("tts", text=ctx.script, output_path=path)
        if result.success:
            ctx.voiceover = result.data
        else:
            ctx.warnings.append(f"TTS failed: {result.error}")
        return ctx


class MusicStage(PipelineStage):
    name = "music"
    skippable = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.package_dir:
            return ctx
        from .capability_registry import build_default_registry
        path = os.path.join(ctx.package_dir, "music_track.mp3")
        result = build_default_registry().call("music", emotion=ctx.plan.get("mood", "dramatic"), output_path=path)
        if result.success:
            ctx.music_track = result.data
        else:
            ctx.warnings.append(f"Music fetch failed: {result.error}")
        return ctx


class QCStage(PipelineStage):
    name = "quality_control"
    skippable = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.skip_qc:
            ctx.qc_report = {"skipped": True}
            return ctx
        if not ctx.package_dir:
            raise RuntimeError("Quality control requires package_dir")
        from .quality_control import run_final_checks
        ctx.qc_report = run_final_checks(ctx.package_dir)
        if not ctx.qc_report.get("ok", True):
            ctx.warnings.extend(str(x) for x in ctx.qc_report.get("notes", []))
        return ctx


class MetadataStage(PipelineStage):
    name = "metadata"
    skippable = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        ctx.metadata = {
            "title": ctx.plan.get("title", ctx.topic),
            "topic": ctx.topic,
            "target_seconds": ctx.target_seconds,
            "generated_at": datetime.now().isoformat(),
            "has_voiceover": ctx.voiceover is not None,
            "has_music": ctx.music_track is not None,
            "thumbnail_variant": ctx.thumbnail_variant,
        }
        if ctx.package_dir:
            with open(os.path.join(ctx.package_dir, "metadata.json"), "w", encoding="utf-8") as handle:
                json.dump(ctx.metadata, handle, indent=2)
        return ctx


class MetricsStage(PipelineStage):
    name = "metrics"
    skippable = True

    def run(self, ctx: PipelineContext) -> PipelineContext:
        planned_count = len(ctx.edit_plan)
        planned_total = sum(float(s.get("duration", 0)) for s in ctx.edit_plan if isinstance(s, dict))
        planned_cpm = planned_count / (ctx.target_seconds / 60.0) if ctx.target_seconds else 0.0
        rendered = 0
        rendered_duration = 0.0
        clips_dir = os.path.join(ctx.package_dir, "_clips") if ctx.package_dir else None
        if clips_dir and os.path.isdir(clips_dir):
            from .segment_engine import get_duration_safe
            for name in os.listdir(clips_dir):
                if name.startswith("segment_") and name.endswith(".mp4"):
                    rendered += 1
                    rendered_duration += max(0.0, float(get_duration_safe(os.path.join(clips_dir, name))))
        actual_cpm = rendered / (rendered_duration / 60.0) if rendered_duration > 0 else 0.0
        ctx.metrics = {
            "planned_filter_count": planned_count,
            "planned_avg_shot_duration": planned_total / max(planned_count, 1),
            "planned_cuts_per_minute": planned_cpm,
            "actual_rendered_segments": rendered,
            "actual_duration_seconds": rendered_duration,
            "actual_cuts_per_minute": actual_cpm,
            "filter_count": rendered,
            "avg_shot_duration": rendered_duration / max(rendered, 1),
            "cuts_per_minute": actual_cpm,
            "has_voiceover": ctx.voiceover is not None,
            "has_music": ctx.music_track is not None,
            "render_time_seconds": self._render_time(ctx),
            "thumbnail_variant": ctx.thumbnail_variant,
        }
        if ctx.package_dir:
            with open(os.path.join(ctx.package_dir, "metrics.json"), "w", encoding="utf-8") as handle:
                json.dump(ctx.metrics, handle, indent=2)
        return ctx

    @staticmethod
    def _render_time(ctx: PipelineContext) -> float:
        result = ctx.stage_results.get("auto_edit", {})
        return float(result.get("elapsed_seconds", 0.0))


def build_director_pipeline(
    skip_stages: Optional[List[str]] = None,
    verbose: bool = True,
    progress_callback: Optional[ProgressCallback] = None,
) -> Pipeline:
    all_stages = [
        ResearchStage(), PlanStage(), ScriptStage(), ThumbnailStage(),
        AutoEditStage(), VoiceoverStage(), MusicStage(), QCStage(),
        MetadataStage(), MetricsStage(),
    ]
    skip_set = {s.strip() for s in (skip_stages or []) if s.strip()}
    return Pipeline(
        [stage for stage in all_stages if stage.name not in skip_set],
        verbose=verbose,
        progress_callback=progress_callback,
    )


def run_step(name: str, func: Callable, severity: Severity, *args, **kwargs) -> StepResult:
    try:
        return StepResult.success(output=func(*args, **kwargs))
    except Exception as exc:
        notes = [str(exc)]
        if severity == Severity.CRITICAL:
            return StepResult.critical(notes=notes)
        if severity == Severity.DEGRADED:
            return StepResult.degraded(notes=notes)
        return StepResult.optional(notes=notes)
