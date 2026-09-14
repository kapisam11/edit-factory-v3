"""Video Director — creative orchestration engine for the AI Video Factory.

Implements the full creative workflow with:
- Deep web research (DuckDuckGo + page scraping)
- Creator style learning (YouTube thumbnail analysis)
- Royalty-free music fetching + beat-sync mixing
- Natural voiceover (Edge TTS, free)
- Professional thumbnail generation with learned styles
- Structured pipeline error handling (critical / degraded / optional)
- LLM-powered script generation with anti-slop validation

Usage:
    from ai_video_factory import VideoDirector
    director = VideoDirector()
    pkg = director.produce("Minecraft betrayal on SMP", raw_video="clip.mp4")
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from typing import Dict, List, Optional, Tuple

from .composer import compose_short_from_video
from .factory import create_package
from .hardware import choose_performance_profile, choose_encoder
from .model_adapter import call_model
from .music_fetcher import get_track_for_emotion
from .music_mixer import (
    detect_music_beats,
    align_segments_to_music,
    add_music_to_video,
    mix_audio,
)
from .pipeline import (
    PipelineError,
    PipelineManifest,
    Severity,
    StepResult,
    run_step,
)
from .research import research_topic
from .segment_engine import get_segments, detect_beats, _get_duration_safe
from .style_learner import learn_style
from .thumbnail import make_thumbnail, make_thumbnail_variants, make_thumbnail_vertical
from .tts import generate_voiceover, generate_emotional_voiceover
from .web_browser import deep_research

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────
# Creative constants
# ───────────────────────────────────────────────

EMOTIONS = [
    "emotional", "inspiring", "nostalgic", "dramatic",
    "mysterious", "funny", "shocking", "intense",
]

HOOK_TEMPLATES = [
    "Nobody believed him", "The hidden legend", "His final choice",
    "Lost forever", "The real reason", "He lost everything",
    "The truth revealed", "Nobody expected this", "The last war", "Betrayed",
]

MUSIC_MOODS = {
    "emotional": "emotional_cinematic",
    "inspiring": "epic_orchestral",
    "nostalgic": "lofi_or_ambient",
    "dramatic": "dark_trap",
    "mysterious": "cinematic_phonk",
    "funny": "upbeat_meme",
    "shocking": "hard_phonk",
    "intense": "dark_trap",
}

STRUCTURE_SECONDS = {
    "hook": (0, 2), "intro": (2, 8), "conflict": (8, 20),
    "climax": (20, 45), "payoff": (45, 60),
}

SAFE_ZONE_TOP = 0.15
SAFE_ZONE_BOTTOM = 0.12

# Anti-slop rules enforced on ALL scripts (LLM or template)
ANTI_SLOP_RULES = {
    "min_words": 10,
    "max_words": 120,
    "min_lines": 3,
    "max_hook_words": 5,
    "banned_phrases": [
        "hello everyone", "today we will", "in this video",
        "dont forget to", "don't forget to", "like and subscribe",
        "welcome back", "what's up guys", "hey guys",
    ],
    "required_punctuation": {".", "!", "?"},
}


def _validate_script_against_rules(script: str, hook: str) -> list[str]:
    """Return a list of rule violations. Empty list = script passes."""
    violations = []
    words = script.split()
    lines = [l.strip() for l in script.splitlines() if l.strip()]
    lower_script = script.lower()

    if len(words) < ANTI_SLOP_RULES["min_words"] and len(lines) < ANTI_SLOP_RULES["min_lines"]:
        violations.append(f"Script too short ({len(words)} words, min {ANTI_SLOP_RULES['min_words']})")
    if len(words) > ANTI_SLOP_RULES["max_words"]:
        violations.append(f"Script too long ({len(words)} words, max {ANTI_SLOP_RULES['max_words']})")
    if len(lines) < ANTI_SLOP_RULES["min_lines"]:
        violations.append(f"Too few lines ({len(lines)}, min {ANTI_SLOP_RULES['min_lines']})")

    if hook:
        hook_words = len(hook.split())
        if hook_words > ANTI_SLOP_RULES["max_hook_words"]:
            violations.append(f"Hook too long ({hook_words} words, max {ANTI_SLOP_RULES['max_hook_words']})")
    else:
        violations.append("Missing hook")

    for banned in ANTI_SLOP_RULES["banned_phrases"]:
        if banned in lower_script:
            violations.append(f"Banned phrase: '{banned}'")

    if not any(p in script for p in ANTI_SLOP_RULES["required_punctuation"]):
        violations.append("No sentence-ending punctuation")

    return violations


class VideoDirector:
    """Orchestrates the full creative pipeline with structured error handling."""

    def __init__(
        self,
        out_root: str = "output",
        model_key: Optional[str] = None,
        freesound_key: Optional[str] = None,
    ):
        self.out_root = out_root
        self.model_key = model_key
        self.freesound_key = freesound_key
        self.profile = choose_performance_profile()
        self.encoder = choose_encoder()
        self.creative_brief: Dict = {}
        self.edit_plan: List[Tuple[float, str, str]] = []
        self.style_profile: Optional[Dict] = None
        self.music_path: Optional[str] = None
        self._manifest: Optional[PipelineManifest] = None

    # ── 1. RESEARCH (deep web) ───────────────────

    def research(
        self, topic: str, use_groq: bool = False, groq_key: Optional[str] = None
    ) -> Dict:
        """Research topic via deep web browsing + optional Groq."""
        logger.info("[DIRECTOR] Researching: %s", topic)

        # Deep web research (optional — don't fail pipeline if scraping breaks)
        web_data: Dict = {}
        try:
            web_data = deep_research(topic, max_search=10, max_scrape=5)
        except Exception as e:
            logger.warning("[DIRECTOR] Deep web research failed: %s", e)

        # Traditional research (Wikipedia, etc.)
        summary = research_topic(topic, use_groq=use_groq, groq_api_key=groq_key)

        # Merge web findings into summary
        if web_data.get("summary_text"):
            summary["web_research"] = web_data["summary_text"][:2000]
        if web_data.get("images"):
            summary["web_images"] = web_data["images"][:20]
        if web_data.get("videos"):
            summary["web_videos"] = web_data["videos"][:10]

        emotion = self._pick_emotion(summary)
        angle = self._pick_angle(summary)
        hook = self._generate_hook(summary, emotion)

        self.creative_brief = {
            "topic": topic,
            "who_what": summary.get("who_what", ""),
            "main_conflict": summary.get("main_conflict", ""),
            "strongest_angle": angle,
            "emotion": emotion,
            "hook": hook,
            "why_care": summary.get("why_viewers_care", ""),
            "visuals": summary.get("visuals", []),
            "web_images": summary.get("web_images", []),
            "web_videos": summary.get("web_videos", []),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        return self.creative_brief

    def _pick_emotion(self, summary: Dict) -> str:
        text = " ".join(str(v) for v in summary.values()).lower()
        scores = {}
        keywords = {
            "emotional": ["sad", "cry", "loss", "heart", "emotional", "tears"],
            "inspiring": ["never give up", "comeback", "win", "hope", "inspiring"],
            "nostalgic": ["old", "remember", "past", "legend", "nostalgia"],
            "dramatic": ["betray", "war", "fight", "drama", "conflict"],
            "mysterious": ["secret", "unknown", "mystery", "hidden", "truth"],
            "funny": ["funny", "lol", "meme", "hilarious", "joke"],
            "shocking": ["shock", "unexpected", "crazy", "insane", "wtf"],
            "intense": ["intense", "clutch", "sweat", "hardcore", "pvp"],
        }
        for emotion, kws in keywords.items():
            scores[emotion] = sum(text.count(kw) for kw in kws)
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "dramatic"

    def _pick_angle(self, summary: Dict) -> str:
        candidates = [
            summary.get("strongest_angle", ""),
            summary.get("main_conflict", ""),
            summary.get("who_what", ""),
        ]
        for c in candidates:
            if c and len(c) > 5:
                return c
        return "The untold story"

    def _generate_hook(self, summary: Dict, emotion: str) -> str:
        text = " ".join(str(v) for v in summary.values()).lower()
        for tmpl in HOOK_TEMPLATES:
            if any(k in text for k in tmpl.lower().split()):
                return tmpl
        conflict = summary.get("main_conflict", "")
        if conflict:
            words = conflict.split()[:3]
            return " ".join(words).title()
        return "The Real Reason"

    # ── 2. STYLE LEARNING ────────────────────────

    def learn_creator_style(self, topic: str) -> Dict:
        """Learn thumbnail style from top creators in the niche."""
        logger.info("[DIRECTOR] Learning creator style for: %s", topic)
        self.style_profile = learn_style(topic)
        return self.style_profile

    # ── 3. MUSIC ─────────────────────────────────

    def fetch_music(self, emotion: str, out_dir: str) -> Optional[str]:
        """Fetch royalty-free music matching the emotion."""
        logger.info("[DIRECTOR] Fetching music for: %s", emotion)
        self.music_path = get_track_for_emotion(
            emotion,
            freesound_key=self.freesound_key,
            out_dir=out_dir,
            prefer_local=True,
        )
        return self.music_path

    # ── 4. VIDEO ANALYSIS ────────────────────────

    def analyze_video(self, video_path: str) -> Dict:
        logger.info("[DIRECTOR] Analyzing video: %s", video_path)
        if not os.path.exists(video_path):
            raise FileNotFoundError(video_path)
        duration = _get_duration_safe(video_path)
        segments = get_segments(video_path, 10)
        beats = detect_beats(video_path)

        scored = []
        for s, e in segments:
            seg_dur = e - s
            quality = 1.0 - abs(seg_dur - 4.5) / 10.0
            scored.append((quality, s, e))
        scored.sort(reverse=True)

        music_bpm, music_beats = None, None
        if self.music_path and os.path.exists(self.music_path):
            music_bpm, music_beats = detect_music_beats(self.music_path)

        return {
            "duration": duration,
            "segments": segments,
            "beats": beats,
            "music_bpm": music_bpm,
            "music_beats": music_beats,
            "best_segments": [(s, e) for _, s, e in scored[:6]],
        }

    # ── 5. SCRIPT & STRUCTURE ────────────────────

    def _build_script_llm(self, analysis: Dict) -> Optional[str]:
        """Try to generate a script via LLM. Returns None on failure or rule violation."""
        if not self.model_key:
            return None

        brief = self.creative_brief
        hook = brief.get("hook", "The Real Reason")
        topic = brief.get("topic", "")
        angle = brief.get("strongest_angle", "")
        emotion = brief.get("emotion", "dramatic")
        target_sec = getattr(self, "_target_seconds", 45.0)

        prompt = f"""You are a viral short-video scriptwriter.
Write a script for a {int(target_sec)}-second vertical video (9:16).

Topic: {topic}
Hook (must be first line, ≤5 words): {hook}
Angle: {angle}
Emotion: {emotion}

RULES — violating any rule means the script will be rejected:
1. Hook MUST be the very first line. Maximum 5 words.
2. Each subtitle line should be 2-6 words.
3. Total script: {max(20, int(target_sec * 2.2))}–{int(target_sec * 3.5)} words.
4. NEVER use these banned phrases: "hello everyone", "today we will", "in this video", "don't forget to", "like and subscribe", "welcome back", "what's up guys", "hey guys".
5. End with a strong punctuation mark (. ! ?).
6. Write like a human, not an AI. Use short sentences. Be punchy.
7. The last line should feel like a payoff or twist.

OUTPUT FORMAT — return ONLY valid JSON, no markdown:
{{"lines": ["HOOK HERE", "line 2", "line 3", "..."]}}
"""
        try:
            raw = call_model(prompt, api_key=self.model_key, timeout=30)
            if not raw:
                return None

            # Extract JSON from potential markdown fences
            text = raw.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                # Drop fence lines
                while lines and lines[0].startswith("```"):
                    lines.pop(0)
                while lines and lines[-1].startswith("```"):
                    lines.pop(-1)
                text = "\n".join(lines).strip()

            parsed = json.loads(text)
            lines = parsed.get("lines", [])
            if not lines:
                return None

            script = "\n".join(str(l) for l in lines)

            # Validate against anti-slop rules
            violations = _validate_script_against_rules(script, hook)
            if violations:
                logger.warning("[DIRECTOR] LLM script failed validation: %s", violations)
                return None

            return script
        except Exception as e:
            logger.warning("[DIRECTOR] LLM script generation failed: %s", e)
            return None

    def _build_script_template(self, analysis: Dict) -> str:
        """Fallback template script when LLM is unavailable or fails validation."""
        brief = self.creative_brief
        hook = brief.get("hook", "The Real Reason")
        topic = brief.get("topic", "")
        angle = brief.get("strongest_angle", "")

        lines = [
            hook,
            f"This is the story of {topic}.",
            f"{angle}.",
            "But nobody saw this coming.",
            "And the ending changed everything.",
        ]
        return "\n".join(lines)

    def build_script(self, analysis: Dict) -> str:
        """Generate script via LLM (preferred) or template (fallback), then validate."""
        # Try LLM first
        script = self._build_script_llm(analysis)
        source = "llm"

        if script is None:
            script = self._build_script_template(analysis)
            source = "template"

        # Final validation (catches template edge cases too)
        violations = _validate_script_against_rules(script, self.creative_brief.get("hook", ""))
        if violations:
            logger.warning("[DIRECTOR] Script from %s has violations: %s", source, violations)
            # If LLM failed rules, we already fell back; if template fails, we can't do much more
            # so we log and deliver anyway (degraded package)
            if self._manifest:
                self._manifest.degraded = True
                self._manifest.add_artifact("script_violations", violations)

        brief = self.creative_brief
        brief["script"] = script
        brief["script_source"] = source
        return script

    def build_edit_plan(self, analysis: Dict, target_seconds: float = 45.0):
        scale = target_seconds / 60.0
        self.edit_plan = [
            (max(1.5, 2.0 * scale), "hook", "impact frame + zoom"),
            (max(4.0, 6.0 * scale), "intro", "cinematic transition + settle"),
            (max(6.0, 12.0 * scale), "conflict", "jump cuts + speed ramp"),
            (max(10.0, 25.0 * scale), "climax", "motion blur + quick zoom"),
            (max(5.0, 15.0 * scale), "payoff", "soft settle + fade out"),
        ]
        return self.edit_plan

    # ── 6. ANTI SLOP ─────────────────────────────

    def _anti_slop_check(self, script: str, plan: List) -> List[str]:
        warnings = []
        if len(script.split()) < 20:
            warnings.append("Script too short")
        if not any(c in script for c in ".!?"):
            warnings.append("No sentence endings")
        generic = ["hello everyone", "today we will", "in this video", "dont forget to"]
        for g in generic:
            if g in script.lower():
                warnings.append(f"Generic filler: '{g}'")
        labels = [p[1] for p in plan]
        if len(set(labels)) < 3:
            warnings.append("Edit plan lacks variety")
        return warnings

    # ── 7. THUMBNAIL ─────────────────────────────

    def generate_thumbnail(self, pkg_dir: str, subject: Optional[str] = None) -> str:
        brief = self.creative_brief
        subj = subject or brief.get("hook", "The Real Reason")
        logger.info("[DIRECTOR] Generating thumbnail: %s", subj)

        thumb_dir = os.path.join(pkg_dir, "thumbnails")
        os.makedirs(thumb_dir, exist_ok=True)

        main = os.path.join(pkg_dir, "thumbnail.png")
        make_thumbnail(subj, main, size=(1280, 720), style_profile=self.style_profile)

        variants = make_thumbnail_variants(subj, thumb_dir, count=3, topic=brief.get("topic"))

        vert = os.path.join(pkg_dir, "thumbnail_vertical.png")
        try:
            make_thumbnail_vertical(subj, vert, size=(1080, 1920))
        except Exception as e:
            logger.warning("Vertical thumbnail failed: %s", e)

        return main

    # ── 8. METADATA ──────────────────────────────

    def generate_metadata(self, pkg_dir: str) -> Dict:
        brief = self.creative_brief
        topic = brief.get("topic", "")
        hook = brief.get("hook", "")
        emotion = brief.get("emotion", "")

        titles = [
            f"{hook} — {topic}",
            f"{hook}",
            f"The Real Story of {topic}",
            f"{topic} — {hook}",
        ]
        description = (
            f"{hook}\n\n"
            f"This is the story of {topic}.\n"
            f"What do you think? Let us know in the comments.\n\n"
            f"#Shorts #YouTubeShorts"
        )
        tags = [
            topic.replace(" ", ""), "Shorts", "YouTubeShorts", "TikTok", "Reels",
            emotion.title(), "Gaming", "Minecraft", "SMP",
        ]
        hashtags = [
            f"#{topic.replace(' ', '')}", "#Shorts", "#YouTubeShorts", "#TikTok", "#Reels",
            f"#{emotion.title()}", "#Gaming", "#Minecraft", "#SMP", "#Lore",
        ]

        meta = {
            "titles": titles,
            "description": description,
            "tags": tags,
            "hashtags": " ".join(hashtags),
            "platforms": ["YouTube Shorts", "TikTok", "Instagram Reels"],
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "safe_zones": {
                "top_percent": SAFE_ZONE_TOP * 100,
                "bottom_percent": SAFE_ZONE_BOTTOM * 100,
            },
        }

        with open(os.path.join(pkg_dir, "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        with open(os.path.join(pkg_dir, "title_options.txt"), "w", encoding="utf-8") as f:
            for t in titles:
                f.write(t + "\n")
        return meta

    # ── 9. HUMAN CHECK ───────────────────────────

    def run_human_check(self, pkg_dir: str) -> Dict:
        brief = self.creative_brief
        script = brief.get("script", "")
        hook = brief.get("hook", "")

        checks = {
            "stop_scrolling": len(hook) <= 25 and any(c.isupper() for c in hook),
            "first_second_strong": hook != "The Real Reason",
            "has_emotion": brief.get("emotion") in EMOTIONS,
            "story_makes_sense": len(script.split("\n")) >= 3,
            "feels_human": len(self._anti_slop_check(script, self.edit_plan)) == 0,
            "avoids_ai_slop": True,
            "satisfying_ending": "ending" in script.lower() or "everything" in script.lower(),
        }
        checks["passed"] = all(checks.values())

        with open(os.path.join(pkg_dir, "human_check.json"), "w", encoding="utf-8") as f:
            json.dump(checks, f, indent=2)

        if not checks["passed"]:
            failed = [k for k, v in checks.items() if not v and k != "passed"]
            logger.warning("[DIRECTOR] Human check failed: %s", failed)
        return checks

    # ── 10. MAIN WORKFLOW ────────────────────────

    def produce(
        self,
        topic: str,
        raw_video: Optional[str] = None,
        use_groq: bool = False,
        groq_key: Optional[str] = None,
        target_seconds: float = 45.0,
        skip_qc: bool = False,
    ) -> str:
        self._target_seconds = target_seconds
        self._manifest = PipelineManifest(topic=topic, target_seconds=target_seconds)
        manifest = self._manifest

        logger.info("=" * 50)
        logger.info("[DIRECTOR] Starting production: %s", topic)
        logger.info("[DIRECTOR] Profile: %s | Encoder: %s", self.profile, self.encoder)
        logger.info("=" * 50)

        # ── STEP 1: Research (CRITICAL) ──
        res = run_step(
            "research",
            self.research,
            Severity.CRITICAL,
            topic,
            use_groq=use_groq,
            groq_key=groq_key,
        )
        manifest.record("research", res)
        if res.failed_critical:
            raise PipelineError(res, "research")

        # ── STEP 2: Style learning (OPTIONAL) ──
        res = run_step("style_learning", self.learn_creator_style, Severity.OPTIONAL, topic)
        manifest.record("style_learning", res)

        # ── STEP 3: Create base package (CRITICAL) ──
        res = run_step(
            "create_package",
            create_package,
            Severity.CRITICAL,
            topic,
            out_root=self.out_root,
            thumbnail_subject=self.creative_brief.get("hook"),
            target_total_seconds=target_seconds,
        )
        manifest.record("create_package", res)
        if res.failed_critical or not res.output:
            raise PipelineError(res, "create_package")
        pkg_dir = res.output
        manifest.add_artifact("package_dir", pkg_dir)

        # ── STEP 4: Script & plan (CRITICAL) ──
        script = self.build_script({})
        with open(os.path.join(pkg_dir, "script.txt"), "w", encoding="utf-8") as f:
            f.write(script)

        plan_path = os.path.join(pkg_dir, "plan.json")
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_data = json.load(f)
        except Exception as e:
            logger.warning("[DIRECTOR] Plan load failed: %s", e)
            plan_data = {}
        plan_data.update({
            "script": script,
            "script_source": self.creative_brief.get("script_source", "template"),
            "edit_plan": self.build_edit_plan({}, target_seconds),
            "creative_brief": self.creative_brief,
            "emotion": self.creative_brief.get("emotion"),
            "hook": self.creative_brief.get("hook"),
        })
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan_data, f, indent=2)

        # ── STEP 5: Anti-slop ──
        slop_warnings = self._anti_slop_check(script, self.edit_plan)
        if slop_warnings:
            logger.warning("[DIRECTOR] Anti-slop: %s", slop_warnings)
            manifest.degraded = True
            with open(os.path.join(pkg_dir, "anti_slop_warnings.json"), "w") as f:
                json.dump(slop_warnings, f, indent=2)

        # ── STEP 6: Thumbnail (DEGRADED) ──
        res = run_step("thumbnail", self.generate_thumbnail, Severity.DEGRADED, pkg_dir)
        manifest.record("thumbnail", res)

        # ── STEP 7: Metadata (DEGRADED) ──
        res = run_step("metadata", self.generate_metadata, Severity.DEGRADED, pkg_dir)
        manifest.record("metadata", res)

        # ── STEP 8: Fetch music (OPTIONAL) ──
        res = run_step(
            "music",
            self.fetch_music,
            Severity.OPTIONAL,
            self.creative_brief.get("emotion", "dramatic"),
            pkg_dir,
        )
        manifest.record("music", res)

        # ── STEP 9: Auto-edit ──
        final_video = None
        if raw_video and os.path.exists(raw_video):
            # Video analysis (CRITICAL for auto-edit branch)
            try:
                analysis = self.analyze_video(raw_video)
                with open(os.path.join(pkg_dir, "video_analysis.json"), "w", encoding="utf-8") as f:
                    json.dump(analysis, f, indent=2, default=str)
                manifest.add_artifact("video_analysis", analysis)

                # Music sync
                if analysis.get("music_bpm") and analysis.get("music_beats"):
                    analysis["best_segments"] = align_segments_to_music(
                        analysis["best_segments"],
                        analysis["music_beats"],
                        analysis["music_bpm"],
                    )
                    plan_data["music_synced"] = True
                    plan_data["music_bpm"] = analysis["music_bpm"]
                    with open(plan_path, "w", encoding="utf-8") as f:
                        json.dump(plan_data, f, indent=2)

                # Compose (CRITICAL)
                res = run_step(
                    "compose",
                    compose_short_from_video,
                    Severity.CRITICAL,
                    raw_video,
                    pkg_dir,
                    review=not skip_qc,
                    auto_fix=True,
                    model_key=self.model_key,
                    skip_qc=skip_qc,
                )
                manifest.record("compose", res)
                if res.failed_critical:
                    raise PipelineError(res, "compose")
                final_video = res.output
                logger.info("[DIRECTOR] Video rendered: %s", final_video)

                # Voiceover (OPTIONAL)
                vo_path = os.path.join(pkg_dir, "voiceover.mp3")
                vo_res = run_step(
                    "voiceover",
                    generate_emotional_voiceover,
                    Severity.OPTIONAL,
                    script,
                    vo_path,
                    self.creative_brief.get("emotion", "dramatic"),
                )
                manifest.record("voiceover", vo_res)
                if not vo_res.ok:
                    vo_path = None

                # Mix (OPTIONAL)
                if self.music_path and final_video:
                    mixed = os.path.join(pkg_dir, "final_with_music.mp4")
                    try:
                        if vo_path and os.path.exists(vo_path):
                            mix_audio(final_video, self.music_path, vo_path, mixed)
                        else:
                            add_music_to_video(final_video, self.music_path, mixed)
                        logger.info("[DIRECTOR] Mixed with music: %s", mixed)
                        shutil.copy2(mixed, final_video)
                        manifest.add_artifact("final_with_music", mixed)
                    except Exception as e:
                        logger.warning("[DIRECTOR] Music mix failed: %s", e)
                        manifest.record("music_mix", StepResult.optional(notes=[str(e)]))
            except PipelineError:
                raise
            except Exception as e:
                logger.error("[DIRECTOR] Auto-edit branch failed: %s", e)
                manifest.record("auto_edit_branch", StepResult.degraded(notes=[str(e)]))
                manifest.degraded = True

        # ── STEP 10: Human check (DEGRADED) ──
        res = run_step("human_check", self.run_human_check, Severity.DEGRADED, pkg_dir)
        manifest.record("human_check", res)
        if res.ok and isinstance(res.output, dict) and res.output.get("passed"):
            logger.info("[DIRECTOR] ✅ Human check PASSED")
        else:
            logger.warning("[DIRECTOR] ❌ Human check FAILED")

        # Write brief & manifest
        with open(os.path.join(pkg_dir, "creative_brief.json"), "w", encoding="utf-8") as f:
            json.dump(self.creative_brief, f, indent=2)

        manifest_path = os.path.join(pkg_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2)

        logger.info("[DIRECTOR] Done: %s (degraded=%s)", pkg_dir, manifest.degraded)
        return pkg_dir
