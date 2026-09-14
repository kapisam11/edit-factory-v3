"""AI Video Factory — automated short video production system.

Main entry points:
    VideoDirector              — compatibility director; footage runs through the production path
    run_production_pipeline    — footage-aware end-to-end production path
    compose_short_from_video   — low-level composition helper
"""
import builtins
import logging
import os
import tempfile


def configure_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


configure_logging()
builtins.tempfile = tempfile

from .director import VideoDirector as _LegacyVideoDirector
from .factory import create_package
from .composer import compose_short_from_video
from .production_pipeline import run_production_pipeline
from .production_models import EditTimeline, Scene, TimelineSegment


class VideoDirector(_LegacyVideoDirector):
    """Public director API with the modern production pipeline as the footage path.

    Existing topic-only callers retain the legacy package flow. Supplying raw
    footage switches to the single-source production orchestrator so scene
    intelligence, recommendations, speech intelligence, music planning and
    rendering are not duplicated across two independent directors.
    """

    def produce(self, topic, raw_video=None, use_groq=False, groq_key=None,
                target_seconds=45.0, skip_qc=False):
        if not raw_video:
            return super().produce(
                topic,
                raw_video=None,
                use_groq=use_groq,
                groq_key=groq_key,
                target_seconds=target_seconds,
                skip_qc=skip_qc,
            )

        package_dir = os.path.join(self.out_root, self._safe_package_name(topic))
        research_summary = None
        try:
            research_summary = self.research(topic, use_groq=use_groq, groq_key=groq_key)
        except Exception as exc:
            logging.getLogger(__name__).warning("Modern director research fallback: %s", exc)

        result = run_production_pipeline(
            raw_video,
            topic,
            package_dir,
            target_seconds=target_seconds,
            research_summary=research_summary,
            model_key=self.model_key,
            skip_qc=skip_qc,
        )
        self._last_production_result = result
        self.creative_brief = research_summary or {}
        if result.errors:
            raise RuntimeError("Production pipeline failed: " + "; ".join(result.errors))
        return result.package_dir

    @staticmethod
    def _safe_package_name(topic):
        import re
        value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(topic).strip()).strip("._-")
        return value[:70] or "video"


# The roadmap runtime adapter is deliberately loaded from the package entry
# point so all public imports see the same pacing and upload-package contracts.
from .roadmap_runtime import install as _install_roadmap_runtime
_install_roadmap_runtime()
from .roadmap_runtime_bindings import install as _install_roadmap_bindings
_install_roadmap_bindings()


__version__ = "2.2.0"
__all__ = [
    "VideoDirector",
    "create_package",
    "compose_short_from_video",
    "run_production_pipeline",
    "Scene",
    "TimelineSegment",
    "EditTimeline",
]
