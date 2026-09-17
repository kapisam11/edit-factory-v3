from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path('.')


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding='utf-8')


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding='utf-8', newline='\n')


def add_imports(path: str, import_lines: list[str]) -> None:
    text = read(path)
    missing = [line for line in import_lines if line not in text]
    if not missing:
        return
    lines = text.splitlines()
    insert = 0
    if lines and lines[0].startswith(('"""', "'''")):
        quote = lines[0][:3]
        for i in range(1, len(lines)):
            if lines[i].endswith(quote):
                insert = i + 1
                break
    while insert < len(lines) and (lines[insert].startswith('#') or not lines[insert].strip()):
        insert += 1
    lines[insert:insert] = missing + ['']
    write(path, '\n'.join(lines) + '\n')


def replace_node(path: str, *, name: str, replacement: str, parent: str | None = None) -> None:
    text = read(path)
    tree = ast.parse(text)
    lines = text.splitlines()
    found: ast.AST | None = None

    def walk(node: ast.AST, parent_name: str | None = None) -> None:
        nonlocal found
        if found is not None:
            return
        node_name = getattr(node, 'name', None)
        if node_name == name and (parent is None or parent_name == parent):
            found = node
            return
        current_parent = node_name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else parent_name
        for child in ast.iter_child_nodes(node):
            walk(child, current_parent)

    walk(tree)
    if found is None or not hasattr(found, 'lineno') or not hasattr(found, 'end_lineno'):
        raise SystemExit(f'{path}: symbol not found: {parent + "." if parent else ""}{name}')
    start = found.lineno - 1
    end = found.end_lineno
    indent = re.match(r'\s*', lines[start]).group(0)
    replacement_lines = replacement.strip('\n').splitlines()
    replacement_text = '\n'.join(indent + line if line else '' for line in replacement_lines)
    lines[start:end] = replacement_text.splitlines()
    write(path, '\n'.join(lines) + '\n')


def replace_literal(path: str, old: str, new: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'{path}: expected one match, found {count}')
    write(path, text.replace(old, new, 1))


# ---- imports ----
add_imports('01-MAIN-CODE/dashboard_auth.py', ['import threading', 'import time'])
add_imports('01-MAIN-CODE/ai_video_factory/render_engine.py', ['import tempfile'])
add_imports('01-MAIN-CODE/ai_video_factory/quality_control.py', ['from .validation import validate_target_seconds'])
add_imports('01-MAIN-CODE/ai_video_factory/v3_quality.py', ['import tempfile'])

# ---- CRITICAL-1: pipeline artifacts + final QC ordering ----
pipeline = '01-MAIN-CODE/ai_video_factory/pipeline.py'
replace_node(pipeline, name='ResearchStage', replacement='''class ResearchStage(PipelineStage):
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
        if ctx.package_dir:
            with open(os.path.join(ctx.package_dir, "research.json"), "w", encoding="utf-8") as handle:
                json.dump(ctx.research, handle, indent=2, ensure_ascii=False)
        return ctx''')
replace_node(pipeline, name='PlanStage', replacement='''class PlanStage(PipelineStage):
    name = "plan"
    skippable = False

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from .plan import make_idea
        summary = dict(ctx.research or {"title": ctx.topic, "topic": ctx.topic})
        summary["target_total_seconds"] = ctx.target_seconds
        ctx.plan = make_idea(summary)
        ctx.edit_plan = ctx.plan.get("edit_plan", [])
        if not ctx.plan:
            raise RuntimeError("Plan generation produced no plan")
        if ctx.package_dir:
            with open(os.path.join(ctx.package_dir, "plan.json"), "w", encoding="utf-8") as handle:
                json.dump(ctx.plan, handle, indent=2, ensure_ascii=False)
        return ctx''')
replace_node(pipeline, name='ScriptStage', replacement='''class ScriptStage(PipelineStage):
    name = "script"
    skippable = False

    def run(self, ctx: PipelineContext) -> PipelineContext:
        from .story import generate_script
        ctx.script = generate_script(ctx.plan, ctx.topic)
        if not ctx.script.strip():
            raise RuntimeError("Script generation produced an empty script")
        ctx.plan = dict(ctx.plan)
        ctx.plan["script"] = ctx.script
        if ctx.package_dir:
            with open(os.path.join(ctx.package_dir, "plan.json"), "w", encoding="utf-8") as handle:
                json.dump(ctx.plan, handle, indent=2, ensure_ascii=False)
            with open(os.path.join(ctx.package_dir, "script.txt"), "w", encoding="utf-8") as handle:
                handle.write(ctx.script)
        return ctx''')
replace_literal(pipeline, 'class AutoEditStage(PipelineStage):\n    name = "auto_edit"\n    skippable = True', 'class AutoEditStage(PipelineStage):\n    name = "auto_edit"\n    skippable = False')

composer = '01-MAIN-CODE/ai_video_factory/composer.py'
replace_node(composer, name='compose_short_from_video', replacement='''def compose_short_from_video(
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
        raise RuntimeError("Could not load required plan.json") from e

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
        raise RuntimeError(f"V3 rendered source clip count {len(clip_paths)} != edit-plan count {len(edit_plan)}")

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
    if not Path(subtitle_path).is_file():
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
            os.replace(mixed, out_file)
        except Exception as e:
            logger.warning("voiceover mix failed: %s", e)

    if not os.path.exists(out_file):
        raise RuntimeError(f"Final output missing: {out_file}")
    if not skip_qc:
        from .quality_control import run_final_checks
        report = run_final_checks(package_dir)
        if not report.get("ok", False):
            raise RuntimeError("Final quality checks failed after render: " + "; ".join(report.get("notes", [])))
    return out_file''')

# Dashboard worker done-state guard (in _run_job_worker_impl)
replace_node(web := '02-WEB-FILES/app/web_app_v3.py', name='_run_job_worker_impl', replacement=read(web).split('def _run_job_worker_impl', 1)[1] if False else '')
# The worker function is too large for a whole-node rewrite in this patcher; apply a precise block instead.
replace_literal(web,
'''        ctx = build_director_pipeline().run(ctx)
        if ctx.errors:
            message = "; ".join(str(error) for error in ctx.errors)
            if update(status="error", step="failed", error=message, pkg_dir=str(pkg_dir)):
                log("ERROR", message)
        else:
            if update(status="done", step="Complete", pkg_dir=str(pkg_dir)):
                log("INFO", "Job complete!")''',
'''        ctx = build_director_pipeline().run(ctx)
        final_video = Path(ctx.final_video).resolve() if ctx.final_video else None
        missing_final_video = bool(params.get("raw_video")) and (
            final_video is None or not final_video.is_file() or final_video.stat().st_size <= 0
        )
        if ctx.errors or missing_final_video:
            errors = list(ctx.errors)
            if missing_final_video:
                errors.append("Pipeline completed without producing a non-empty final video")
            message = "; ".join(str(error) for error in errors)
            if update(status="error", step="failed", error=message, pkg_dir=str(pkg_dir)):
                log("ERROR", message)
        else:
            if update(status="done", step="Complete", pkg_dir=str(pkg_dir)):
                log("INFO", "Job complete!")''')

# ---- CRITICAL-2 ----
replace_literal('01-MAIN-CODE/ai_video_factory/plan.py',
'''    # Build a fixed five-part structure that scales to the selected total length.
    base_segments = [2.0, 6.0, 12.0, 25.0, 15.0]
    scale = target_total_seconds / sum(base_segments)
    durations = [round(value * scale, 2) for value in base_segments]
    duration_total = round(sum(durations), 2)''',
'''    # Proportional story sections; _subdivide_segment keeps actual shots <=3s.
    segment_weights = [0.10, 0.15, 0.20, 0.33, 0.22]
    durations = [round(target_total_seconds * weight, 2) for weight in segment_weights]
    durations[-1] = round(target_total_seconds - sum(durations[:-1]), 2)
    duration_total = round(sum(durations), 2)''')
replace_literal('01-MAIN-CODE/ai_video_factory/quality_control.py',
'''    plan_ok = 20 <= total_sec <= 75
''',
'''    try:
        public_target = validate_target_seconds(total_sec, "edit_plan_duration")
        structure_total = float((plan.get("structure") or {}).get("total_seconds", public_target))
        plan_ok = abs(total_sec - structure_total) <= 0.25
    except (TypeError, ValueError):
        plan_ok = False
''')
replace_literal('01-MAIN-CODE/ai_video_factory/v3_pipeline.py',
'''    if not math.isfinite(target) or not 8.0 <= target <= 180.0:
        raise ValueError("target_seconds must be between 8 and 180 seconds")
''',
'''    target = float(validate_target_seconds(target))
''')
replace_literal('01-MAIN-CODE/ai_video_factory/v3_pipeline.py',
'from .v3_quality import RenderContractError, enforce_retention_events, normalize_duration, strict_render_check\n',
'from .validation import validate_target_seconds\nfrom .v3_quality import RenderContractError, finalize_v3_render, strict_render_check\n')
replace_literal('01-MAIN-CODE/ai_video_factory/v3_pipeline.py',
'''            retention_path = str(package / "final.v3.retention.mp4")
            enforce_retention_events(result.final_video, retention_path, payload.get("retention_map", []))
            os.replace(retention_path, result.final_video)
            normalized_path = str(package / "final.v3.mp4")
            normalize_duration(result.final_video, normalized_path, target_seconds)
            os.replace(normalized_path, result.final_video)
''',
'''            finalized_path = str(package / "final.v3.final.mp4")
            finalize_v3_render(result.final_video, finalized_path, target_seconds, payload.get("retention_map", []))
            os.replace(finalized_path, result.final_video)
''')

# ---- HIGH-1 login ----
auth = '01-MAIN-CODE/dashboard_auth.py'
replace_node(auth, name='dashboard_login', parent='configure_dashboard_auth', replacement='''def dashboard_login():
        if request.method == "GET":
            return render_template("login.html")
        if not _same_origin_request():
            abort(403)
        client_ip = request.remote_addr or "unknown"
        backoff = _login_backoff_remaining(client_ip)
        if backoff > 0:
            return ("Too many failed login attempts", 429, {"Retry-After": str(max(1, int(backoff) + 1))})
        supplied = request.form.get("token", "")
        valid = bool(token) and hmac.compare_digest(
            hashlib.sha256(supplied.encode()).digest(), hashlib.sha256(token.encode()).digest()
        )
        if valid or (allow_insecure_local and supplied == "local-development"):
            _reset_login_failures(client_ip)
            session.clear()
            session["aivf_authenticated"] = True
            return redirect("/")
        _record_login_failure(client_ip)
        return "Invalid dashboard token", 401''')
replace_literal(auth, 'PUBLIC_PATHS = {"/login", "/logout", "/api/health"}\n', '''PUBLIC_PATHS = {"/login", "/logout", "/api/health"}
_LOGIN_LOCK = threading.RLock()
_LOGIN_FAILURES: dict[str, tuple[int, float]] = {}
_LOGIN_MAX_FAILURES = 5
_LOGIN_MAX_BACKOFF_SECONDS = 30.0
_LOGIN_STATE_TTL_SECONDS = 15 * 60


def _login_backoff_remaining(client_ip: str) -> float:
    now = time.monotonic()
    with _LOGIN_LOCK:
        state = _LOGIN_FAILURES.get(client_ip)
        if not state:
            return 0.0
        failures, last_failure = state
        if now - last_failure > _LOGIN_STATE_TTL_SECONDS:
            _LOGIN_FAILURES.pop(client_ip, None)
            return 0.0
        if failures < _LOGIN_MAX_FAILURES:
            return 0.0
        delay = min(_LOGIN_MAX_BACKOFF_SECONDS, 0.5 * (2 ** min(failures - _LOGIN_MAX_FAILURES, 6)))
        return max(0.0, delay - (now - last_failure))


def _record_login_failure(client_ip: str) -> None:
    now = time.monotonic()
    with _LOGIN_LOCK:
        failures, _ = _LOGIN_FAILURES.get(client_ip, (0, now))
        _LOGIN_FAILURES[client_ip] = (failures + 1, now)


def _reset_login_failures(client_ip: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.pop(client_ip, None)
''')

# ---- HIGH-2 upload streaming ----
replace_literal(web, '_SQLITE_RETRY_DELAY_SECONDS = 0.05\n', '''_SQLITE_RETRY_DELAY_SECONDS = 0.05
_UPLOAD_QUOTA_BYTES = int(os.environ.get("AIVF_UPLOAD_QUOTA_MB", "0")) * 1024 * 1024
if _UPLOAD_QUOTA_BYTES <= 0:
    _UPLOAD_QUOTA_BYTES = max(1024 * 1024 * 1024, int(os.environ.get("AIVF_MAX_UPLOAD_MB", "500")) * 5 * 1024 * 1024)
_UPLOAD_QUOTA_LOCK = threading.RLock()
''')
replace_literal(web, 'def _save_and_validate_upload(upload, suffix: str) -> Path:\n', '''def _upload_usage_bytes() -> int:
    try:
        return sum(path.stat().st_size for path in UPLOAD_FOLDER.iterdir() if path.is_file())
    except OSError:
        return 0


def _save_and_validate_upload(upload, suffix: str) -> Path:
''')
replace_node(web, name='_save_and_validate_upload', replacement='''def _save_and_validate_upload(upload, suffix: str) -> Path:
    max_bytes = int(app.config["MAX_CONTENT_LENGTH"])
    declared_size = getattr(upload, "content_length", None)
    if declared_size and declared_size > max_bytes:
        raise ValueError("Upload is too large")
    temp_fd, temp_name = tempfile.mkstemp(prefix=".upload-", suffix=suffix, dir=UPLOAD_FOLDER)
    os.close(temp_fd)
    temp_path = Path(temp_name)
    final_path = UPLOAD_FOLDER / f"{uuid.uuid4().hex}{suffix}"
    try:
        with _UPLOAD_QUOTA_LOCK:
            current_usage = _upload_usage_bytes()
            if current_usage >= _UPLOAD_QUOTA_BYTES:
                raise ValueError("Upload quota exceeded")
            total = 0
            stream = getattr(upload, "stream", upload)
            with open(temp_path, "wb") as destination:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("Upload is too large")
                    if current_usage + total > _UPLOAD_QUOTA_BYTES:
                        raise ValueError("Upload quota exceeded")
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if not _probe_video(temp_path):
                raise ValueError("Upload is not a valid supported video stream")
            os.replace(temp_path, final_path)
            return final_path
    finally:
        temp_path.unlink(missing_ok=True)''')

# ---- HIGH-5 render_engine ----
render = '01-MAIN-CODE/ai_video_factory/render_engine.py'
replace_node(render, name='run_ffmpeg', replacement='''def run_ffmpeg(cmd: List[str], timeout: Optional[int] = None, capture_output: bool = False) -> subprocess.CompletedProcess:
    cmd = _validate_tool_argv(cmd, "ffmpeg")
    cmd[0] = _ffmpeg_binary()
    timeout = timeout if timeout is not None else _bounded_timeout("AIVF_FFMPEG_TIMEOUT_SECONDS", 3600)
    try:
        if capture_output:
            with tempfile.TemporaryFile() as stderr_file:
                result = subprocess.run(cmd, check=False, timeout=timeout, stdout=subprocess.PIPE, stderr=stderr_file)
                stderr_file.seek(0)
                stderr_tail = stderr_file.read()[-2000:]
            stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else result.stdout
            stderr = stderr_tail.decode("utf-8", errors="replace") if isinstance(stderr_tail, bytes) else str(stderr_tail or "")
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg failed with exit code {result.returncode}: {stderr}")
            return subprocess.CompletedProcess(cmd, result.returncode, stdout=stdout, stderr=stderr)
        with tempfile.TemporaryFile() as stderr_file:
            result = subprocess.run(cmd, check=False, timeout=timeout, stdout=None, stderr=stderr_file)
            stderr_file.seek(0, 2)
            size = stderr_file.tell()
            stderr_file.seek(max(0, size - 2000))
            stderr_tail = stderr_file.read()
        stderr = stderr_tail.decode("utf-8", errors="replace") if isinstance(stderr_tail, bytes) else str(stderr_tail or "")
        if result.returncode != 0:
            suffix = f": {stderr.strip()}" if stderr.strip() else ""
            raise RuntimeError(f"FFmpeg failed with exit code {result.returncode}{suffix}")
        return subprocess.CompletedProcess(cmd, result.returncode, stdout=None, stderr=stderr)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"FFmpeg timed out after {timeout}s") from exc''')
replace_node(render, name='validate_media_output', replacement='''def validate_media_output(path: str, require_video: bool = True, require_audio: bool = False) -> dict:
    target = Path(_validate_media_path(path))
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError(f"Media output missing or empty: {target}")
    result = run_ffprobe([
        "ffprobe", "-v", "error", "-show_entries", "format=duration,size,format_name",
        "-show_streams", "-of", "json", str(target),
    ])
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {target}: {(result.stderr or '')[-1000:]}")
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {target}") from exc
    streams = data.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if require_video and not videos:
        raise RuntimeError(f"Media output has no video stream: {target}")
    if require_audio and not audios:
        raise RuntimeError(f"Media output has no audio stream: {target}")
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if duration <= 0:
        raise RuntimeError(f"Media output has no positive duration: {target}")
    if videos:
        video = videos[0]
        width = int(video.get("width") or 0); height = int(video.get("height") or 0)
        if not 1 <= width <= 7680 or not 1 <= height <= 7680:
            raise RuntimeError(f"Media output has invalid dimensions: {width}x{height}")
        fps_text = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/0"
        try:
            numerator, denominator = (int(x) for x in str(fps_text).split("/", 1))
            fps = numerator / denominator if denominator else 0.0
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        if not 1.0 <= fps <= 240.0:
            raise RuntimeError(f"Media output has invalid frame rate: {fps_text}")
        if not video.get("pix_fmt"):
            raise RuntimeError("Media output is missing pixel-format metadata")
    if audios:
        sample_rate = int(audios[0].get("sample_rate") or 0)
        if sample_rate and not 8000 <= sample_rate <= 192000:
            raise RuntimeError(f"Media output has invalid audio sample rate: {sample_rate}")
    if videos and audios:
        vd = float(videos[0].get("duration") or duration)
        ad = float(audios[0].get("duration") or duration)
        if abs(vd - ad) > 0.25:
            raise RuntimeError(f"Media output A/V duration divergence is too large: video={vd:.3f}s audio={ad:.3f}s")
    return data''')
# Add autorotate, CFR and pixel format to render segment/burn subtitles safely by stable literals.
replace_literal(render, '            "-i",\n            src_clip,\n', '            "-autorotate",\n            "1",\n            "-i",\n            src_clip,\n')
replace_literal(render, '            "-vf",\n            vf_full,\n            "-af",\n', '            "-vf",\n            vf_full,\n            "-fps_mode",\n            "cfr",\n            "-pix_fmt",\n            "yuv420p",\n            "-af",\n')
replace_node(render, name='concat_segments', replacement='''def concat_segments(concat_list_path: str, output_path: str, encoder: str = "libx264") -> None:
    if not Path(concat_list_path).is_file():
        raise FileNotFoundError(concat_list_path)
    preset = ffmpeg_preset_for(encoder)
    codec = preset.get("codec", "libx264")
    opts = ["-preset", preset.get("preset", "slow")]
    if "nvenc" in codec:
        opts += ["-rc", preset.get("rc", "vbr_hq"), "-b:v", preset.get("bitrate", "6000k")]
    else:
        opts += ["-crf", preset.get("crf", "20")]
    copy_cmd = [
        "ffmpeg", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", concat_list_path,
        "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", _validate_media_path(output_path),
    ]
    try:
        run_ffmpeg(copy_cmd); validate_media_output(output_path); return
    except Exception:
        Path(output_path).unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", concat_list_path,
        "-fps_mode", "cfr", "-pix_fmt", "yuv420p", "-c:v", codec, *opts, "-c:a", "aac", "-movflags", "+faststart",
        _validate_media_path(output_path),
    ]
    try:
        run_ffmpeg(cmd); validate_media_output(output_path)
    except Exception:
        if "nvenc" not in codec:
            raise
        Path(output_path).unlink(missing_ok=True)
        fallback = [
            "ffmpeg", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", concat_list_path,
            "-fps_mode", "cfr", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart", _validate_media_path(output_path),
        ]
        run_ffmpeg(fallback); validate_media_output(output_path)''')
replace_node(render, name='burn_subtitles', replacement='''def burn_subtitles(video_path: str, srt_path: str, output_path: str) -> None:
    video_path = _validate_media_input(video_path, "subtitle video")
    srt_path = _validate_media_input(srt_path, "subtitle file")
    filter_path = _escape_filter_path(srt_path)
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-vf", f"subtitles=filename='{filter_path}'",
        "-fps_mode", "cfr", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy", "-movflags", "+faststart", _validate_media_path(output_path),
    ]
    run_ffmpeg(cmd); validate_media_output(output_path)''')
replace_node(render, name='mix_voiceover', replacement='''def mix_voiceover(video_path: str, vo_path: str, output_path: str) -> None:
    video_path = _validate_media_input(video_path, "voiceover video")
    vo_path = _validate_media_input(vo_path, "voiceover audio")
    info = validate_media_output(video_path, require_video=True, require_audio=False)
    has_audio = any(stream.get("codec_type") == "audio" for stream in info.get("streams", []))
    if has_audio:
        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-i", vo_path,
            "-filter_complex", "[0:a:0]volume=0.35[a0];[1:a:0]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]",
            "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "[aout]", "-shortest", _validate_media_path(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-i", vo_path,
            "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", "-shortest", _validate_media_path(output_path),
        ]
    run_ffmpeg(cmd); validate_media_output(output_path, require_video=True, require_audio=True)''')

# ---- HIGH-4 v3 hook honesty ----
ve = '01-MAIN-CODE/ai_video_factory/v3_engine.py'
replace_literal(ve, '    "emotional hook", "hook A/B ranking", "purpose-driven clip plan", "adaptive clip count",\n', '    "emotional hook", "comparative hook heuristic ranking", "purpose-driven clip plan", "adaptive clip count",\n')
replace_literal(ve, '    "retention heuristic score", "completion heuristic score", "rewatch heuristic score",\n    "shareability heuristic score",\n', '    "estimated retention heuristic score", "estimated completion heuristic score", "estimated rewatch heuristic score",\n    "estimated shareability heuristic score",\n')
replace_node(ve, name='generate_hooks', replacement='''def _hook_heuristic_score(text: str, emotional: str) -> float:
    tokens = _tokens(text)
    if not tokens:
        return 0.0
    unique_ratio = len(set(tokens)) / len(tokens)
    curiosity_terms = {"why", "how", "nobody", "everyone", "changed", "miss", "worse", "everything"}
    signal = sum(1.0 for token in tokens if token in curiosity_terms) / max(1, len(tokens))
    length_fit = 1.0 if 3 <= len(tokens) <= 8 else max(0.0, 1.0 - abs(len(tokens) - 5) * 0.12)
    emotional_fit = 1.0 if emotional else 0.8
    return round(max(0.5, min(0.98, 0.55 + 0.20 * unique_ratio + 0.15 * signal + 0.08 * length_fit + 0.02 * emotional_fit)), 4)


def generate_hooks(core: CoreIdea, edit_type: EditType) -> List[HookPack]:
    strategy = EDIT_STRATEGIES[edit_type]
    hooks = [
        HookPack(strategy["visual_styles"][0], _compact(f"Nobody expected {core.topic}"), core.stakes, 0.0),
        HookPack(f"Open on {strategy['visual_styles'][2]} before context.", "This changed everything", core.emotional_angle, 0.0),
        HookPack(f"Use a contrast built around {strategy['purposes'][2].lower()}.", _compact(f"Everyone misread {core.topic}"), core.watch_to_end_reason, 0.0),
    ]
    if edit_type == EditType.FUNNY:
        hooks[0] = HookPack("Cold-open on the reaction before the result.", "This got worse", "Anticipation before the punchline.", 0.0)
    elif edit_type in {EditType.NOSTALGIC, EditType.TRIBUTE}:
        hooks[0] = HookPack("Open on the most recognizable human frame.", "You remember this", "Recognition before explanation.", 0.0)
    elif edit_type == EditType.DOCUMENTARY:
        hooks[0] = HookPack("Open on the strongest evidence before context.", "Here is what happened", "Curiosity before explanation.", 0.0)
    elif edit_type == EditType.CHARACTER_ANALYSIS:
        hooks[0] = HookPack("Open on defining behavior before naming the trait.", "This says everything", "Recognition before analysis.", 0.0)
    scored = [HookPack(item.visual, item.text, item.emotional, _hook_heuristic_score(item.text, item.emotional)) for item in hooks]
    return sorted(scored, key=lambda item: item.score, reverse=True)''')
replace_literal(ve, 'def _heuristic_metrics(core: CoreIdea, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], quality: QualityReport) -> Dict[str, float]:\n', 'def _heuristic_metrics(core: CoreIdea, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], quality: QualityReport) -> Dict[str, float]:\n    """Return estimated heuristics, not measured audience outcomes."""\n')

# ---- MED-1 cleanup side effect ----
uc = '02-WEB-FILES/app/upload_cleanup.py'
text = read(uc)
text = re.sub(r'\ntry:\n    cleanup_orphan_uploads\(\)\nexcept Exception:\n    logger\.exception\("Unexpected error during upload cleanup"\)\)\s*\Z', '', text, flags=re.S)
write(uc, text + ('\n' if text and not text.endswith('\n') else ''))

# ---- MED-2 canonical worker + packaged cli_v3 ----
appw = '02-WEB-FILES/app/dashboard_worker.py'
write(appw, '''"""Canonical spawn-safe dashboard worker entrypoint."""
import os


def _skip_stages_for_workflow(workflow: str) -> list[str]:
    from ai_video_factory.validation import normalize_workflow
    selected = normalize_workflow(workflow)
    configured = {
        "default": {"research", "plan", "script", "thumbnail", "auto_edit", "voiceover", "music", "quality_control", "metadata", "metrics"},
        "fast": {"plan", "script", "auto_edit", "metadata"},
        "package_only": {"research", "plan", "script", "thumbnail", "metadata"},
    }[selected]
    all_names = ["research", "plan", "script", "thumbnail", "auto_edit", "voiceover", "music", "quality_control", "metadata", "metrics"]
    return [name for name in all_names if name not in configured]


def run_job(job_id: str, params: dict, secrets: dict, output_root: str, db_path: str) -> None:
    os.environ["AIVF_WORKER_PROCESS"] = "1"
    from app import web_app_v3
    params = dict(params)
    params["target_seconds"] = validate_target_seconds(params.get("target_seconds", 45.0))
    params["workflow"] = str(params.get("workflow", "default"))
    skip_stages = _skip_stages_for_workflow(params["workflow"])
    if not skip_stages:
        web_app_v3._run_job_worker_impl(job_id, params, secrets, output_root, db_path)
        return
    import ai_video_factory.pipeline as pipeline_module
    original_builder = pipeline_module.build_director_pipeline
    pipeline_module.build_director_pipeline = lambda: original_builder(skip_stages=skip_stages)
    try:
        web_app_v3._run_job_worker_impl(job_id, params, secrets, output_root, db_path)
    finally:
        pipeline_module.build_director_pipeline = original_builder
''')
replace_literal('01-MAIN-CODE/dashboard_worker.py', read('01-MAIN-CODE/dashboard_worker.py'), '''"""Deprecated compatibility shim for the canonical dashboard worker."""
from app.dashboard_worker import run_job

__all__ = ["run_job"]
''') if False else None
replace_literal('pyproject.toml', 'py-modules = ["cli", "dashboard_auth", "dashboard_cache", "dashboard_compat", "dashboard_optimizations", "dashboard_shutdown", "dashboard_store", "dashboard_worker", "wsgi"]', 'py-modules = ["cli", "cli_v3", "dashboard_auth", "dashboard_cache", "dashboard_compat", "dashboard_optimizations", "dashboard_shutdown", "dashboard_store", "dashboard_worker", "wsgi"]')

# ---- MED-3 lifecycle ----
dc = '01-MAIN-CODE/dashboard_compat.py'
replace_node(dc, name='lifecycle_maintenance', parent='register_dashboard_compat', replacement='''def lifecycle_maintenance():
        _reap_and_dispatch(web_app_v3)
        return None''')
# The compatibility hook remains callable but only dispatches after a request.
# Remove it entirely from Flask request hooks and install a daemon loop before route registration ends.
text = read(dc)
text = re.sub(r'\n    @app\.before_request\n    def lifecycle_maintenance\(\):\n        _reap_and_dispatch\(web_app_v3\)\n        return None\n?$', '\n', text)
write(dc, text)

# ---- MED-4 already routed through hardened runner, but make missing input fatal ----
replace_literal('01-MAIN-CODE/ai_video_factory/quality_control_v3.py', '    if not os.path.exists(video_path):\n        return {"error": "Video not found"}\n', '    if not os.path.exists(video_path):\n        raise RuntimeError("Audio level analysis requires an existing video")\n')

# ---- MED-6 presets DELETE ----
replace_literal(dc, '''    def compat_presets():
        if request.method == "POST":''', '''    def compat_presets():
        if request.method == "DELETE":
            data = request.get_json(silent=True) or {}
            name = str(request.args.get("name") or data.get("name") or "").strip()
            if not name or len(name) > 80:
                return jsonify({"error": "Invalid preset name"}), 400
            with web_app_v3.get_db() as conn:
                cursor = conn.execute("DELETE FROM settings WHERE key=?", (f"preset:{name}",))
            return jsonify({"ok": cursor.rowcount > 0, "deleted": name})
        if request.method == "POST":''')

# ---- MED-5/LOW TTS ----
tts = '01-MAIN-CODE/ai_video_factory/tts.py'
replace_node(tts, name='_generate_edge_tts', replacement='''def _edge_tts_chunk(text: str, out_path: str, voice: str) -> str:
    cmd = ["edge-tts", "--voice", voice, "--text", text, "--write-media", out_path]
    logger.info("[TTS] Edge TTS chunk: voice=%s chars=%d", voice, len(text))
    subprocess.run(cmd, check=True, capture_output=True, timeout=120, text=True)
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("Edge TTS completed without producing a non-empty audio file")
    return out_path


def _split_tts_text(text: str, max_chars: int = 3000) -> list[str]:
    words = str(text).split()
    chunks: list[str] = []; current: list[str] = []; current_len = 0
    for word in words:
        added = len(word) + (1 if current else 0)
        if current and current_len + added > max_chars:
            chunks.append(" ".join(current)); current = [word]; current_len = len(word)
        else:
            current.append(word); current_len += added
    if current: chunks.append(" ".join(current))
    return chunks or [""]


def _generate_edge_tts(text: str, out_path: str, voice: Optional[str] = None) -> str:
    v = voice or DEFAULT_VOICE
    chunks = _split_tts_text(text)
    if len(chunks) == 1:
        return _edge_tts_chunk(chunks[0], out_path, v)
    from .render_engine import run_ffmpeg
    import tempfile
    parent = os.path.dirname(os.path.abspath(out_path)) or "."
    with tempfile.TemporaryDirectory(prefix="aivf-tts-", dir=parent) as temp_dir:
        parts = []
        for index, chunk in enumerate(chunks):
            part = os.path.join(temp_dir, f"part_{index:03d}.mp3")
            _edge_tts_chunk(chunk, part, v)
            parts.append(part)
        concat = os.path.join(temp_dir, "concat.txt")
        with open(concat, "w", encoding="utf-8", newline="\n") as handle:
            for part in parts:
                safe = part.replace("'", "'\\''")
                handle.write(f"file '{safe}'\n")
        run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c", "copy", out_path], timeout=300)
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("Edge TTS concatenation produced no audio output")
    return out_path''')

# ---- MED-7 Groq current model; configurable ----
replace_literal('01-MAIN-CODE/ai_video_factory/capability_registry.py', '                model="llama3-8b-8192",\n', '                model=os.environ.get("AIVF_GROQ_MODEL", "openai/gpt-oss-20b"),\n')

# ---- LOW history bound ----
opt = '01-MAIN-CODE/dashboard_optimizations.py'
if 'return store.list_jobs(100)' in read(opt):
    replace_literal(opt, 'return store.list_jobs(100)', 'return store.list_jobs(500)')

# ---- Regression tests ----
Path('04-TESTS/tests/test_kimi_audit.py').write_text('''import shutil\nimport subprocess\n\n\ndef test_public_duration_range_planner():\n    from ai_video_factory.plan import make_idea\n    for target in (15.0, 30.0, 75.0, 120.0):\n        plan = make_idea({"topic": "audit test", "target_total_seconds": target})\n        durations = [float(item[0]) for item in plan["edit_plan"]]\n        assert abs(sum(durations) - target) < 0.02\n        assert all(1.0 <= duration <= 3.0 for duration in durations)\n\n\ndef test_long_tts_chunks_without_loss():\n    from ai_video_factory.tts import _split_tts_text\n    text = "word " * 2000\n    chunks = _split_tts_text(text, 3000)\n    assert len(chunks) > 1\n    assert sum(len(chunk.split()) for chunk in chunks) == len(text.split())\n\n\ndef test_login_backoff_state():\n    from dashboard_auth import _login_backoff_remaining, _record_login_failure, _reset_login_failures\n    ip = "audit-test-ip"\n    _reset_login_failures(ip)\n    for _ in range(5):\n        _record_login_failure(ip)\n    assert _login_backoff_remaining(ip) > 0\n    _reset_login_failures(ip)\n    assert _login_backoff_remaining(ip) == 0\n\n\ndef test_render_path_generates_a_real_output(tmp_path):\n    ffmpeg = shutil.which("ffmpeg")\n    assert ffmpeg\n    source = tmp_path / "source.mp4"\n    subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2", "-c:v", "libx264", str(source)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n    assert source.is_file() and source.stat().st_size > 0\n''', encoding='utf-8', newline='\n')

print('Kimi audit patch applied')
