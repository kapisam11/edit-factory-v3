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
        except Exception as exc:
            logger.warning("Could not load timeline.json; falling back to generic segments: %s", exc)
    return get_segments(input_video, fallback_count)


def compose_short_from_video(
    input_video: str,
    package_dir: str,
    out_file: Optional[str] = None,
    review: bool = True,
    auto_fix: bool = False,
    model_key: Optional[str] = None,
    skip_qc: bool = False,
) -> str:
    _ensure_dir(package_dir)
    if out_file is None:
        out_file = os.path.join(package_dir, "final_short.mp4")

    edit_plan = [(6, "segment")]
    script = ""
    if not skip_qc:
        try:
            from .quality_control import run_final_checks
            checks = run_final_checks(package_dir)
            if not checks.get("ok", False):
                raise RuntimeError("Final quality checks failed: " + "; ".join(checks.get("notes", [])))
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("Final checks could not run: %s", e)

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

    try:
        with open(os.path.join(package_dir, "plan.json"), "r", encoding="utf-8") as f:
            plan = json.load(f)
        edit_plan = plan.get("edit_plan", edit_plan)
        script = plan.get("script", script)
    except Exception as e:
        logger.warning("Could not load plan.json: %s", e)

    segments = _load_source_segments(package_dir, len(edit_plan), input_video)
    temp_dir = os.path.join(package_dir, "_clips")
    _ensure_dir(temp_dir)
    beats = detect_beats(input_video)
    clip_paths = generate_clip_paths(input_video, segments, len(edit_plan), temp_dir)
    if not clip_paths:
        raise RuntimeError("No clips could be generated from input video.")

    filter_effectiveness = _load_filter_effectiveness(package_dir)
    target_size = (1080, 1920)
    seq_files = []
    durations = []
    for i, seg in enumerate(edit_plan):
        duration = float(seg[0]) if isinstance(seg, (list, tuple)) else float(seg)
        label = seg[1] if isinstance(seg, (list, tuple)) and len(seg) > 1 else "segment"
        durations.append(duration)
        src_clip = clip_paths[i % len(clip_paths)]
        dst = os.path.join(temp_dir, f"segment_{i:02d}.mp4")
        vf = build_cinematic_filter(i, label, duration, filter_effectiveness or None, target_size=target_size)
        seg_start, seg_end = segments[i % len(segments)]
        to = snap_to_beat(seg_start, seg_end, duration, beats)
        clip_dur = _get_duration_safe(src_clip)
        if clip_dur > 0:
            to = min(to, seg_start + clip_dur)
        actual_dur = to - seg_start
        if actual_dur <= 0:
            logger.warning("Segment %d has zero/negative duration, skipping", i)
            continue
        try:
            render_segment(src_clip, seg_start, actual_dur, vf, dst)
            seq_files.append(dst)
        except Exception as exc:
            logger.error("Segment render %d failed: %s", i, exc)

    if not seq_files:
        raise RuntimeError("No segments could be rendered.")
    seq_files = _apply_templates(seq_files, package_dir)

    subtitle_path = os.path.join(package_dir, "captions.ass")
    choreographed = os.path.exists(subtitle_path)
    if not choreographed:
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
        logger.warning("TTS voiceover failed: %s", e)

    concat_out = os.path.join(temp_dir, "concatenated.mp4")
    write_concat_list(seq_files, os.path.join(temp_dir, "concat.txt"))
    concat_segments(os.path.join(temp_dir, "concat.txt"), concat_out)
    burn_subtitles(concat_out, subtitle_path, out_file)

    if has_vo:
        mixed = os.path.join(package_dir, "final_short_vo.mp4")
        try:
            mix_voiceover(out_file, vo_path, mixed)
            return mixed
        except Exception as e:
            logger.error("Voiceover mix failed: %s", e)
    return out_file
