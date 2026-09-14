"""Runtime adapters that make the 40-point roadmap enforceable at runtime."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


def install() -> None:
    from . import content_factory, upload_package

    # ------------------------------------------------------------------
    # Adaptive pacing: exact-duration allocation with hard bounds.
    # ------------------------------------------------------------------
    def adaptive_pacing(
        intensity: Sequence[float],
        *,
        total_seconds: float = 45.0,
        min_shot: float = 0.45,
        max_shot: float = 4.0,
    ) -> List[float]:
        values = [max(0.0, min(1.0, float(v))) for v in intensity] or [0.5]
        total = float(total_seconds)
        if total < min_shot * len(values):
            raise ValueError("total_seconds must be at least min_shot * number of shots")
        durations = [min_shot] * len(values)
        remaining = total - sum(durations)
        weights = [1.25 - value for value in values]
        active = set(range(len(values)))
        while remaining > 1e-9 and active:
            weight = sum(weights[i] for i in active) or float(len(active))
            additions = {}
            exhausted = set()
            for i in active:
                share = remaining * weights[i] / weight
                room = max_shot - durations[i]
                add = min(room, share)
                additions[i] = add
                if room - add <= 1e-9:
                    exhausted.add(i)
            added = sum(additions.values())
            for i, add in additions.items():
                durations[i] += add
            if added <= 1e-9:
                break
            remaining -= added
            active -= exhausted
        rounded = [round(d, 3) for d in durations]
        correction = round(total - sum(rounded), 3)
        for i in sorted(range(len(rounded)), key=lambda j: rounded[j], reverse=True):
            candidate = round(rounded[i] + correction, 3)
            if min_shot - 1e-9 <= candidate <= max_shot + 1e-9:
                rounded[i] = candidate
                break
        if abs(sum(rounded) - total) > 0.01:
            raise RuntimeError(f"adaptive pacing could not allocate exactly {total_seconds}s")
        return rounded

    content_factory.adaptive_pacing = adaptive_pacing

    # ------------------------------------------------------------------
    # Adaptive clip count: the automatic factory is explicitly 5–20.
    # ------------------------------------------------------------------
    def choose_clip_count(speech_words: int, intensity_mean: float, target_seconds: float) -> int:
        rate = float(speech_words) / max(1.0, float(target_seconds))
        if rate > 3.0 or intensity_mean > 0.75:
            return 12
        if rate > 2.3 or intensity_mean > 0.58:
            return 9
        if rate > 1.6:
            return 6
        return 5

    content_factory.choose_clip_count = choose_clip_count

    # ------------------------------------------------------------------
    # Semantic emotion scoring. Use a sentence-transformers model when the
    # optional intelligence dependency is installed; preserve an offline
    # deterministic lexical fallback otherwise.
    # ------------------------------------------------------------------
    _emotion_model: Any = None
    _emotion_model_loaded = False
    emotion_labels = ("dramatic", "hype", "funny", "curious")
    emotion_prototypes = {
        "dramatic": "danger betrayal loss destruction shocking tragic conflict",
        "hype": "victory winning clutch insane exciting intense celebration success",
        "funny": "comedy joke hilarious silly ridiculous awkward amusing fail",
        "curious": "mystery secret hidden truth why how unexpected discovery unknown",
    }

    def _semantic_embeddings(texts: Sequence[str]) -> Optional[List[List[float]]]:
        nonlocal _emotion_model, _emotion_model_loaded
        if not _emotion_model_loaded:
            _emotion_model_loaded = True
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore
                _emotion_model = SentenceTransformer(
                    os.environ.get("AIVF_EMOTION_MODEL", "all-MiniLM-L6-v2")
                )
            except Exception:
                _emotion_model = None
        if _emotion_model is None:
            return None
        try:
            encoded = _emotion_model.encode(list(texts), normalize_embeddings=True)
            return [list(map(float, row)) for row in encoded]
        except Exception:
            return None

    def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    def detect_emotion(text: str) -> dict[str, Any]:
        embeddings = _semantic_embeddings([str(text)] + [emotion_prototypes[label] for label in emotion_labels])
        if embeddings and len(embeddings) == len(emotion_labels) + 1:
            scores = {label: round(_cosine(embeddings[0], embeddings[i + 1]), 6) for i, label in enumerate(emotion_labels)}
            best = max(scores, key=scores.get)
            floor = min(scores.values())
            spread = max(scores.values()) - floor
            confidence = round(spread / max(abs(max(scores.values())), 1e-6), 3)
            return {"emotion": best, "scores": scores, "confidence": max(0.0, min(1.0, confidence)), "method": "sentence_transformers"}
        lowered = re.sub(r"[^a-z0-9!? ]+", " ", str(text).lower())
        phrase_terms = {
            "dramatic": ("lost everything", "changed everything", "betrayed", "went wrong", "final battle", "could not believe"),
            "hype": ("won the game", "clutch moment", "best play", "comeback", "insane win"),
            "funny": ("made everyone laugh", "funniest moment", "failed badly", "looked ridiculous", "could not stop laughing"),
            "curious": ("what really happened", "hidden truth", "nobody knew", "why did", "how did", "secret reason"),
        }
        scores = {label: sum(lowered.count(phrase) for phrase in phrases) for label, phrases in phrase_terms.items()}
        if not any(scores.values()):
            tokens = set(re.findall(r"\b[a-z0-9']+\b", lowered))
            lexical_terms = {
                "dramatic": {"danger", "betray", "lost", "destroyed", "shocked", "tragic", "conflict"},
                "hype": {"win", "winning", "won", "victory", "clutch", "insane", "exciting", "intense", "celebration", "success"},
                "funny": {"funny", "joke", "hilarious", "silly", "ridiculous", "awkward", "amusing", "fail"},
                "curious": {"mystery", "secret", "hidden", "truth", "why", "how", "unexpected", "discovery", "unknown"},
            }
            scores = {label: float(len(tokens.intersection(terms))) for label, terms in lexical_terms.items()}
        best = max(scores, key=lambda label: (scores[label], label))
        total = sum(float(v) for v in scores.values()) or 1.0
        return {"emotion": best, "scores": scores, "confidence": round(float(scores[best]) / total, 3), "method": "phrase_fallback"}

    content_factory.detect_emotion = detect_emotion

    # ------------------------------------------------------------------
    # Render validation: media structure + decode integrity + black-frame
    # detection. This replaces the old metadata-only check.
    # ------------------------------------------------------------------
    original_validate_render = content_factory.validate_render

    def validate_render(path: str, *, expected_width: Optional[int] = None, expected_height: Optional[int] = None, min_duration: float = 0.5) -> dict[str, Any]:
        result = original_validate_render(path, expected_width=expected_width, expected_height=expected_height, min_duration=min_duration)
        if not result.get("ok"):
            return result
        target = Path(path)
        errors = list(result.get("errors", []))
        try:
            decode = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(target), "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
            if decode.returncode != 0 or decode.stderr.strip():
                errors.append("decode_integrity_failure")
        except Exception as exc:
            errors.append(f"decode_check:{exc}")
        try:
            black = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", str(target), "-vf", "blackdetect=d=0.50:pic_th=0.98", "-an", "-f", "null", "-"],
                capture_output=True, text=True, timeout=120,
            )
            black_events = [line for line in black.stderr.splitlines() if "black_start:" in line]
            result["black_frames"] = black_events
            if black_events:
                errors.append("black_frame_detected")
        except Exception as exc:
            errors.append(f"black_frame_check:{exc}")
        result["errors"] = errors
        result["ok"] = not errors
        return result

    content_factory.validate_render = validate_render

    # ------------------------------------------------------------------
    # Upload-package compatibility: keep a stable root-level copy/paste
    # bundle in addition to the platform-specific directories.
    # ------------------------------------------------------------------
    original_finalize = upload_package.finalize_upload_package

    def finalize_with_compatibility(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        manifest = original_finalize(*args, **kwargs)
        package_dir = kwargs.get("package_dir") or (args[0] if args else None)
        if not package_dir:
            return manifest
        root = Path(str(package_dir))
        platform = str(manifest.get("platform", "youtube_shorts"))
        platform_dir = root / "upload" / platform
        upload_dir = root / "upload"
        upload_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("title.txt", "description.txt", "tags.txt", "metadata.json", "README-UPLOAD.md", "captions.srt"):
            source = platform_dir / filename
            target = upload_dir / filename
            if source.exists() and not target.exists():
                shutil.copyfile(source, target)
        return manifest

    upload_package.finalize_upload_package = finalize_with_compatibility

    # ------------------------------------------------------------------
    # True checkpoint reuse for expensive primary renders and platform
    # renders. A completed artifact is reused only after validation.
    # ------------------------------------------------------------------
    from . import complete_factory
    from .production_models import ProductionResult
    original_pipeline = complete_factory.run_production_pipeline
    original_render_all = complete_factory.render_all_platforms
    original_long_form = complete_factory.render_long_form_master
    original_clip_factory = complete_factory.render_clip_factory

    def cached_pipeline(*args: Any, **kwargs: Any) -> Any:
        package_dir = str(args[2]) if len(args) >= 3 else str(kwargs.get("package_dir", ""))
        existing = Path(package_dir) / "final.mp4"
        if existing.exists():
            check = validate_render(str(existing), min_duration=0.25)
            if check.get("ok"):
                return ProductionResult(package_dir=package_dir, final_video=str(existing), script_path=str(Path(package_dir) / "script.txt") if Path(package_dir, "script.txt").exists() else None, metrics_path=str(Path(package_dir) / "metrics.json") if Path(package_dir, "metrics.json").exists() else None)
        return original_pipeline(*args, **kwargs)

    def cached_render_all(source: str, package_dir: str, platforms: Sequence[str], **kwargs: Any) -> Dict[str, str]:
        existing: Dict[str, str] = {}
        all_valid = True
        for platform in platforms:
            path = Path(package_dir) / "renders" / f"{platform}.mp4"
            if path.exists() and validate_render(str(path), expected_width=complete_factory.PLATFORM_LAYOUTS[platform][0], expected_height=complete_factory.PLATFORM_LAYOUTS[platform][1], min_duration=0.25).get("ok"):
                existing[platform] = str(path)
            else:
                all_valid = False
        return existing if all_valid else original_render_all(source, package_dir, platforms, **kwargs)

    def cached_long_form(source: str, package_dir: str) -> str:
        path = Path(package_dir) / "long_form" / "long_form_master.mp4"
        if path.exists() and validate_render(str(path), expected_width=1920, expected_height=1080, min_duration=0.25).get("ok"):
            return str(path)
        return original_long_form(source, package_dir)

    def cached_clip_factory(source: str, package_dir: str, *, count: int, **kwargs: Any) -> List[str]:
        expected = max(5, min(20, int(count)))
        paths = [Path(package_dir) / "clip_factory" / f"short_{i:02d}.mp4" for i in range(1, expected + 1)]
        if all(path.exists() and validate_render(str(path), expected_width=1080, expected_height=1920, min_duration=0.25).get("ok") for path in paths):
            return [str(path) for path in paths]
        return original_clip_factory(source, package_dir, count=count, **kwargs)

    complete_factory.run_production_pipeline = cached_pipeline
    complete_factory.render_all_platforms = cached_render_all
    complete_factory.render_long_form_master = cached_long_form
    complete_factory.render_clip_factory = cached_clip_factory
