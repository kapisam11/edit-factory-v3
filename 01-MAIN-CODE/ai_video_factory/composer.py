"""High-level orchestration for automated short composition."""
import json
import logging
import os
from typing import Optional, Dict, Any, List

from .segment_engine import get_segments, detect_beats, snap_to_beat, generate_clip_paths, _get_duration_safe
from .effects_engine import build_cinematic_filter
from .render_engine import (
    _ensure_dir,
    render_segment,
    write_concat_list,
    concat_segments,
    burn_subtitles,
    mix_voiceover,
)
from .subtitle_tools import script_to_srt
from .tts import generate_voiceover

logger = logging.getLogger(__name__)


def _make_srt_from_script(script: str, durations, out_path: str):
    import re
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    items = []
    t = 0.0
    for i, dur in enumerate(durations):
        if not sentences:
            break
        target_words = max(2, min(6, int(round(len(script.split()) / max(1, len(durations))))))
        chunk_words = []
        while sentences and len(chunk_words) < target_words:
            chunk_words.extend(sentences.pop(0).split())
        if not chunk_words:
            break
        text = " ".join(chunk_words[:target_words * 2])
        start = _sec_to_srt(t)
        t += dur
        end = _sec_to_srt(t)
        items.append((i + 1, start, end, text))
    with open(out_path, "w", encoding="utf-8") as f:
        for idx, start, end, text in items:
            f.write(f"{idx}\n{start} --> {end}\n{text}\n\n")


def _sec_to_srt(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _load_filter_effectiveness(package_dir: str) -> Dict[str, Any]:
    filter_effectiveness = {}
    try:
        knowledge_file = os.path.join(package_dir, "knowledge_context.json")
        if os.path.exists(knowledge_file):
            with open(knowledge_file, "r", encoding="utf-8") as f:
                json.load(f)
            try:
                from .knowledge import KnowledgeBase
                kb = KnowledgeBase()
                filter_effectiveness = kb.filter_effectiveness
            except Exception as e:
                logger.warning("KnowledgeBase load failed: %s", e)
    except Exception as e:
        logger.warning("Knowledge context load failed: %s", e)
    return filter_effectiveness


def _apply_templates(seq_files: List[str], package_dir: str):
    """Apply templates only when the active plan explicitly permits them; fail closed for V3."""
    plan_path = os.path.join(package_dir, "plan.json")
    try:
        with open(plan_path, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
    except Exception as exc:
        if os.path.exists(plan_path):
            raise RuntimeError("Unable to read plan.json while deciding whether legacy templates are permitted") from exc
        plan = {}

    directives = plan.get("v3_directives") if isinstance(plan, dict) else {}
    if isinstance(directives, dict):
        if directives.get("blueprint_contract") and not directives.get("disable_templates", False):
            raise RuntimeError("V3 plan does not explicitly disable legacy templates")
        if directives.get("disable_templates", False):
            return seq_files

    try:
        from .templates import find_templates, apply_overlay
        templates = find_templates()
        if templates:
            templ_dir = os.path.join(package_dir, "_templ")
            _ensure_dir(templ_dir)
            templ_seq = []
            for i, s in enumerate(seq_files):
                overlay = templates[i % len(templates)]
                outp = os.path.join(templ_dir, os.path.basename(s))
                apply_overlay(s, overlay, outp)
                templ_seq.append(outp)
            return templ_seq
    except Exception as e:
        logger.warning("Template overlay failed: %s", e)
    return seq_files


def _load_source_segments(package_dir: str, fallback_count: int, input_video: str):
    timeline_path = os.path.join(package_dir, "timeline.json")
    if os.path.exists(timeline_path):
        try:
            from .edit_planner import load_timeline
            timeline = load_timeline(timeline_path)
            if timeline.segments:
                return [(float(segment.source_start), float(segment.source_end)) for segment in timeline.segments]
            raise ValueError("timeline.json contains no segments")
        except Exception as exc:
            try:
                with open(os.path.join(package_dir, "plan.json"), "r", encoding="utf-8") as handle:
                    plan = json.load(handle)
            except Exception:
                plan = {}
            if isinstance(plan, dict) and isinstance(plan.get("v3_directives"), dict) and plan["v3_directives"].get("blueprint_contract"):
                raise RuntimeError("V3 timeline exists but could not be loaded safely") from exc
            logger.warning("Could not load timeline.json; falling back to generic segments: %s", exc)
    return get_segments(input_video, fallback_count)


def _target_size_from_plan(plan: Dict[str, Any]) -> tuple[int, int]:
    directives = plan.get("v3_directives") if isinstance(plan, dict) else {}
    profile = directives.get("platform_profile") if isinstance(directives, dict) else {}
    try:
        width = int(profile.get("width", 1080))
        height = int(profile.get("height", 1920))
        if width <= 0 or height <= 0:
            raise ValueError
        return width, height
    except (TypeError, ValueError, AttributeError):
        return (1080, 1920)


def compose_short_from_video(
    input_video: str,
    package_dir: str,
    out_file: Optional[str] = None,
    review: bool = True,
    auto_fix: bool = False,
    model_key: Optional[str] = None,
    skip_qc: bool = False,
    strict_voiceover: bool = False,
) -> str:
    _ensure_dir(package_dir)
    if out_file is None:
        out_file = os.path.join(package_dir, "final_short.mp4")

    edit_plan = [(6, "segment")]
    script = ""
    plan: Dict[str, Any] = {}

    if review:
        try:
            from .review import run_review, apply_auto_fixes
            report = run_review(package_dir, use_model=bool(model_key), model_key=model_key or "")
            if not report.get("ok", True) and auto_fix:
                applied = apply_auto_fixes(package_dir)
                if applied:
                    try:
                        with open(os.path.join(package_dir, "plan.json"), "r", encoding="utf-8") as f:
                            plan = json.load(f)
                        edit_plan = plan.get("edit_plan", edit_plan)
                        script = plan.get("script", script)
                    except Exception as e:
                        logger.warning("Could not reload plan after auto-fix: %s", e)
        except Exception as e:
            logger.warning("Review step failed: %s", e)

    plan_path = os.path.join(package_dir, "plan.json")
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
        edit_plan = plan.get("edit_plan", edit_plan)
        script = plan.get("script", script)
    except Exception as e:
        if os.path.exists(plan_path):
            raise RuntimeError("Could not load required plan.json") from e
        logger.warning("Could not load plan.json: %s", e)

    v3_directives = plan.get("v3_directives") if isinstance(plan, dict) else {}
    is_v3 = isinstance(v3_directives, dict) and bool(v3_directives.get("blueprint_contract"))
    if is_v3 and not isinstance(edit_plan, list):
        raise RuntimeError("V3 edit_plan is missing or invalid")

    segments = _load_source_segments(package_dir, len(edit_plan), input_video)
    if is_v3 and len(segments) != len(edit_plan):
        raise RuntimeError(f"V3 source-segment count {len(segments)} != edit-plan count {len(edit_plan)}")

    temp_dir = os.path.join(package_dir, "_clips")
    _ensure_dir(temp_dir)
    beats = detect_beats(input_video)
    clip_paths = generate_clip_paths(input_video, segments, len(edit_plan), temp_dir)
    if not clip_paths:
        raise RuntimeError("No clips could be generated from input video.")
    if is_v3 and len(clip_paths) != len(edit_plan):
        raise RuntimeError(f"V3 source clip count {len(clip_paths)} != edit-plan count {len(edit_plan)}")

    filter_effectiveness = _load_filter_effectiveness(package_dir)
    target_size = _target_size_from_plan(plan)
    seq_files = []
    durations = []
    for i, seg in enumerate(edit_plan):
        duration = float(seg[0]) if isinstance(seg, (list, tuple)) else float(seg)
        label = seg[1] if isinstance(seg, (list, tuple)) and len(seg) > 1 else "segment"
        durations.append(duration)
        src_index = i if is_v3 else i % len(clip_paths)
        segment_index = i if is_v3 else i % len(segments)
        src_clip = clip_paths[src_index]
        dst = os.path.join(temp_dir, f"segment_{i:02d}.mp4")
        vf = build_cinematic_filter(i, label, duration, filter_effectiveness or None, target_size=target_size)
        seg_start, seg_end = segments[segment_index]
        to = snap_to_beat(seg_start, seg_end, duration, beats)
        clip_dur = _get_duration_safe(src_clip)
        if clip_dur > 0:
            to = min(max(to, seg_start + min(duration, clip_dur)), seg_start + clip_dur)
        actual_dur = max(0.01, duration if is_v3 else (to - seg_start))
        try:
            render_segment(src_clip, seg_start, actual_dur, vf, dst)
            seq_files.append(dst)
        except Exception as exc:
            logger.error("Segment render %d failed: %s", i, exc)

    if not seq_files:
        raise RuntimeError("No segments could be rendered.")
    if is_v3 and len(seq_files) != len(edit_plan):
        raise RuntimeError(f"V3 rendered segment count {len(seq_files)} != edit-plan count {len(edit_plan)}")
    if isinstance(plan.get("v3_directives"), dict):
        plan["rendered_segment_count"] = len(seq_files)
        plan["rendered_target_size"] = list(target_size)
        with open(os.path.join(package_dir, "plan.json"), "w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2, ensure_ascii=False)
    seq_files = _apply_templates(seq_files, package_dir)

    subtitle_path = os.path.join(package_dir, "captions.ass")
    if not os.path.exists(subtitle_path):
        subtitle_path = os.path.join(package_dir, "script.srt")
        try:
            script_to_srt(script, subtitle_path)
        except Exception as e:
            logger.warning("subtitle_tools failed (%s), falling back to naive split", e)
            _make_srt_from_script(script, durations, subtitle_path)

    vo_path = os.path.join(package_dir, "voice.mp3")
    has_vo = False
    try:
        generate_voiceover(script, vo_path)
        has_vo = True
    except Exception as e:
        if strict_voiceover:
            raise RuntimeError(f"Voiceover generation failed: {e}") from e
        logger.warning("TTS voiceover failed: %s", e)

    concat_out = os.path.join(temp_dir, "concatenated.mp4")
    write_concat_list(seq_files, os.path.join(temp_dir, "concat.txt"))
    concat_segments(os.path.join(temp_dir, "concat.txt"), concat_out)
    burn_subtitles(concat_out, subtitle_path, out_file)

    if has_vo:
        mixed = os.path.join(package_dir, "final_short_vo.mp4")
        try:
            mix_voiceover(out_file, vo_path, mixed)
            os.replace(mixed, out_file)
        except Exception as e:
            if strict_voiceover:
                raise RuntimeError(f"Voiceover mix failed: {e}") from e
            logger.warning("voiceover mix failed: %s", e)

    if not os.path.isfile(out_file) or os.path.getsize(out_file) <= 0:
        raise RuntimeError(f"Final output missing or empty: {out_file}")

    if not skip_qc:
        try:
            from .quality_control import run_final_checks
            report = run_final_checks(package_dir)
        except Exception as exc:
            raise RuntimeError(f"Final quality checks failed after render: {exc}") from exc
        if not report.get("ok", False):
            raise RuntimeError("Final quality checks failed after render: " + "; ".join(report.get("notes", [])))

    return out_file
