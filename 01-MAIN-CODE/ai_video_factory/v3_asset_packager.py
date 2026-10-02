"""V3 asset packaging service separated from pipeline orchestration."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .production_models import ProductionResult
from .v3_exceptions import V3PackagingError


class V3AssetPackager:
    """Own thumbnail generation and upload-package assembly as one cohesive service."""

    def package(
        self,
        *,
        result: ProductionResult,
        package: Path,
        topic: str,
        platform: str,
        source_video: str,
        baseline_summary: dict[str, Any],
    ) -> None:
        try:
            from .thumbnail import (
                extract_best_video_frame,
                make_thumbnail_variants,
                make_thumbnail_vertical,
                select_best_thumbnail_variant,
            )

            thumb_dir = package / "thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumbnail_work_dir = Path(
                tempfile.mkdtemp(prefix="aivf-v3-thumb-", dir=str(package))
            )
            try:
                frame_path = extract_best_video_frame(source_video, str(thumbnail_work_dir))
                subject = str(
                    baseline_summary.get("hook")
                    or baseline_summary.get("thumbnail")
                    or topic
                ).strip()
                variants = make_thumbnail_variants(
                    subject,
                    str(thumb_dir),
                    count=3,
                    topic=topic,
                    background_path=frame_path,
                )
                if not variants:
                    raise V3PackagingError("thumbnail generation returned no variants")
                selected_variant = select_best_thumbnail_variant(variants)
                if not 1 <= int(selected_variant) <= len(variants):
                    raise V3PackagingError("thumbnail selector returned an invalid variant index")

                thumbnail_path = package / "thumbnail.png"
                shutil.copyfile(variants[int(selected_variant) - 1], thumbnail_path)
                make_thumbnail_vertical(
                    subject,
                    str(package / "thumbnail_vertical.png"),
                    size=(1080, 1920),
                    background_path=frame_path,
                )
                baseline_summary["thumbnail"] = str(thumbnail_path)
                result.artifacts["thumbnail"] = str(thumbnail_path)
                result.artifacts["thumbnail_variants"] = str(thumb_dir)
            finally:
                shutil.rmtree(thumbnail_work_dir, ignore_errors=True)

            from .upload_package import finalize_upload_package

            finalize_upload_package(
                str(package),
                topic=topic,
                summary=baseline_summary,
                script=str(baseline_summary.get("script", "")),
                platform=platform,
                final_video=result.final_video,
                thumbnail=str(baseline_summary.get("thumbnail") or "") or None,
                caption_path=(
                    str(package / "captions.ass")
                    if (package / "captions.ass").exists()
                    else None
                ),
            )
            result.artifacts["upload_package_manifest"] = str(package / "upload_package.json")
        except V3PackagingError:
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            raise V3PackagingError(f"V3 asset packaging failed: {exc}") from exc


__all__ = ["V3AssetPackager"]
