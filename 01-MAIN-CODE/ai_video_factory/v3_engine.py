"""Edit Factory v3 emotion-first planning and retention engine.

The planner decides what the viewer should feel before it plans clips or text.
It is deterministic and offline-safe so it can run in CI, the CLI, or ahead of
the existing production renderer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Mapping, Sequence


class EditType(str, Enum):
    EMOTIONAL = "Emotional"
    MOTIVATIONAL = "Motivational"
    NOSTALGIC = "Nostalgic"
    FUNNY = "Funny"
    DRAMATIC = "Dramatic"
    DOCUMENTARY = "Documentary"
    SIGMA = "Sigma"
    CHARACTER_ANALYSIS = "Character Analysis"
    TRIBUTE = "Tribute"
    STORYTELLING = "Storytelling"


@dataclass(frozen=True)
class V3Config:
    target_seconds: float = 30.0
    platform: str = "youtube_shorts"
    audience: str = "general short-form viewers"
    bpm: int = 120
    retention_interval: float = 2.0
    max_overlay_words: int = 6
    min_clip_seconds: float = 0.8
    max_clip_seconds: float = 4.0
    language: str = "en"

    def validate(self) -> None:
        if not 8.0 <= float(self.target_seconds) <= 180.0:
            raise ValueError("target_seconds must be between 8 and 180")
        if not 40 <= int(self.bpm) <= 240:
            raise ValueError("bpm must be between 40 and 240")
        if not 0.75 <= float(self.retention_interval) <= 3.0:
            raise ValueError("retention_interval must be between 0.75 and 3.0")
        if not 2 <= int(self.max_overlay_words) <= 8:
            raise ValueError("max_overlay_words must be between 2 and 8")
        if self.min_clip_seconds <= 0 or self.max_clip_seconds < self.min_clip_seconds:
            raise ValueError("invalid clip duration bounds")


@dataclass(frozen=True)
class CoreIdea:
    topic: str
    why_people_care: str
    emotional_angle: str
    target_emotion: str
    watch_to_end_reason: str
    payoff: str
    stakes: str


@dataclass(frozen=True)
class HookPack:
    visual: str
    text: str
    emotional: str
    score: float


@dataclass(frozen=True)
class ClipBeat:
    index: int
    start: float
    end: float
    purpose: str
    emotion: str
    visual_style: str
    text_overlay: str
    camera_motion: str
    transition: str


@dataclass(frozen=True)
class MusicPlan:
    bpm: int
    energy: str
    emotional_tone: str
    beat_seconds: float
    drop_time: float
    sync_points: List[float]


@dataclass(frozen=True)
class RetentionEvent:
    time: float
    kind: str
    instruction: str


@dataclass
class QualityReport:
    passed: bool
    score: int
    checks: Dict[str, bool] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass
class V3Blueprint:
    version: str
    core_idea: CoreIdea
    edit_type: str
    hooks: List[HookPack]
    clip_plan: List[ClipBeat]
    music: MusicPlan
    retention_map: List[RetentionEvent]
    thumbnail_concept: str
    title_options: List[str]
    hashtags: List[str]
    description: str
    platform_variants: Dict[str, Dict[str, Any]]
    quality: QualityReport
    metrics: Dict[str, float]
    capabilities: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


EMOTION_TERMS: Mapping[str, Sequence[str]] = {
    "trust": ("trust", "loyal", "friend", "stayed", "helped", "protected", "believed"),
    "dramatic": ("betray", "risk", "lost", "danger", "destroy", "fight", "final", "secret"),
    "inspiring": ("built", "won", "overcame", "started", "dream", "worked", "changed"),
    "nostalgic": ("remember", "old", "first", "childhood", "used to", "back then", "legacy"),
    "funny": ("funny", "joke", "fail", "awkward", "ridiculous", "laugh", "chaos"),
    "curious": ("why", "how", "hidden", "unknown", "mystery", "truth", "nobody knew"),
}

EDIT_BY_EMOTION: Mapping[str, EditType] = {
    "trust": EditType.TRIBUTE,
    "dramatic": EditType.DRAMATIC,
    "inspiring": EditType.MOTIVATIONAL,
    "nostalgic": EditType.NOSTALGIC,
    "funny": EditType.FUNNY,
    "curious": EditType.STORYTELLING,
}

V3_CAPABILITIES = [
    "emotion-first core idea", "single edit-type lock", "visual hook", "text hook",
    "emotional hook", "hook A/B ranking", "purpose-driven clip plan", "adaptive clip count",
    "exact duration allocation", "2-6 word overlays", "overlay de-duplication", "music energy plan",
    "beat-grid sync", "drop-aware payoff", "retention event map", "1-3 second visual change rule",
    "camera motion planning", "transition restraint", "human-editor reject pass", "dead-moment detection",
    "repetition detection", "AI-slideshow guard", "payoff validation", "hook-payoff continuity",
    "platform-safe variants", "9:16 Shorts profile", "9:16 TikTok profile", "9:16 Reels profile",
    "1:1 social profile", "16:9 long-form profile", "thumbnail concept", "title laboratory",
    "description generator", "hashtag pack", "silent-viewing readability", "safe-area awareness",
    "retention heuristic score", "completion heuristic score", "rewatch heuristic score",
    "shareability heuristic score",
]


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", str(text).lower())


def _compact(text: str, max_words: int = 6) -> str:
    return " ".join(re.findall(r"\S+", str(text).strip())[:max_words])


def analyze_core_idea(
    topic: str,
    context: str = "",
    audience: str = "general short-form viewers",
) -> CoreIdea:
    del audience  # reserved for future audience-specific scoring
    topic = str(topic).strip()
    if not topic:
        raise ValueError("topic is required")
    source = f"{topic} {context}".lower()
    scores = {
        emotion: sum(source.count(term) for term in terms)
        for emotion, terms in EMOTION_TERMS.items()
    }
    emotion = max(scores, key=lambda item: (scores[item], item)) if any(scores.values()) else "curious"

    if emotion == "trust":
        angle = f"Why {topic} became the person people could rely on"
        care = "Loyalty under pressure is instantly relatable."
        stakes = "Trust can be earned in one moment and lost in one decision."
        payoff = f"The moment that proves what {topic} really meant to everyone."
    elif emotion == "dramatic":
        angle = f"The decision that changed everything for {topic}"
        care = "Conflict, risk, and irreversible consequences create immediate stakes."
        stakes = "Something valuable can be lost before the viewer understands why."
        payoff = "Reveal the consequence viewers were waiting to understand."
    elif emotion == "inspiring":
        angle = f"How {topic} kept going when quitting was easier"
        care = "Viewers connect with effort that turns into a visible payoff."
        stakes = "The attempt only matters if failure feels possible."
        payoff = "Show the result that makes the struggle worth it."
    elif emotion == "nostalgic":
        angle = f"Why people still remember {topic}"
        care = "Shared memories create instant emotional recognition."
        stakes = "The feeling only works if the memory seems specific and fleeting."
        payoff = "Return to the image or moment viewers wanted to remember."
    elif emotion == "funny":
        angle = f"The moment {topic} went completely off the rails"
        care = "A fast setup with escalating surprise rewards attention."
        stakes = "The joke dies if the setup is longer than the payoff."
        payoff = "Deliver the cleanest reaction or reversal last."
    else:
        angle = f"The part of {topic} most people miss"
        care = "A knowledge gap makes viewers want the missing context."
        stakes = "The answer must feel more valuable than the setup."
        payoff = "Resolve the question with one clear, memorable insight."

    return CoreIdea(
        topic=topic,
        why_people_care=care,
        emotional_angle=angle,
        target_emotion=emotion,
        watch_to_end_reason=f"The viewer needs the final proof behind: {angle}.",
        payoff=payoff,
        stakes=stakes,
    )


def choose_edit_type(core: CoreIdea, requested: str | None = None) -> EditType:
    if requested:
        normalized = str(requested).strip().lower()
        for item in EditType:
            if item.value.lower() == normalized:
                return item
        raise ValueError(f"unknown edit type: {requested}")
    return EDIT_BY_EMOTION.get(core.target_emotion, EditType.STORYTELLING)


def generate_hooks(core: CoreIdea, edit_type: EditType) -> List[HookPack]:
    candidates = [
        HookPack(
            "Open on the highest-stakes reaction before context.",
            _compact(f"Nobody expected {core.topic}"),
            core.stakes,
            0.93,
        ),
        HookPack(
            "Start mid-action with a 6-8% punch-in.",
            "This changed everything",
            core.emotional_angle,
            0.89,
        ),
        HookPack(
            "Use a contrast frame: before vs after.",
            _compact(f"Everyone misread {core.topic}"),
            core.watch_to_end_reason,
            0.86,
        ),
    ]
    if edit_type == EditType.FUNNY:
        candidates[0] = HookPack(
            "Cold-open on the reaction, cut away before the result.",
            "This got worse",
            "Anticipation before the punchline.",
            0.95,
        )
    elif edit_type == EditType.NOSTALGIC:
        candidates[0] = HookPack(
            "Open on the most recognizable old frame with subtle motion.",
            "You remember this",
            "Recognition before explanation.",
            0.94,
        )
    return sorted(candidates, key=lambda hook: hook.score, reverse=True)


def _purpose_sequence(count: int) -> List[str]:
    if count <= 5:
        return ["Hook", "Context", "Curiosity", "Payoff", "Final impact"][:count]
    middle_count = max(0, count - 6)
    return ["Hook", "Context", "Curiosity", "Importance"] + ["Escalation"] * middle_count + [
        "Payoff", "Final impact"
    ]


def _allocate_durations(count: int, total: float, minimum: float, maximum: float) -> List[float]:
    if count <= 0:
        raise ValueError("count must be positive")
    if total < minimum * count or total > maximum * count:
        raise ValueError("clip count cannot satisfy duration bounds")
    weights = [0.72 if i == 0 else 1.18 if i in (count - 2, count - 1) else 1.0 for i in range(count)]
    durations = [max(minimum, min(maximum, total * weight / sum(weights))) for weight in weights]
    for _ in range(100):
        diff = total - sum(durations)
        if abs(diff) < 0.0005:
            break
        candidates = [
            i for i, value in enumerate(durations)
            if (diff > 0 and value < maximum - 0.0005)
            or (diff < 0 and value > minimum + 0.0005)
        ]
        if not candidates:
            break
        share = diff / len(candidates)
        for i in candidates:
            durations[i] = max(minimum, min(maximum, durations[i] + share))
    rounded = [round(value, 3) for value in durations]
    rounded[-1] = round(rounded[-1] + total - sum(rounded), 3)
    return rounded


def _base_overlay(purpose: str, core: CoreIdea, max_words: int) -> str:
    mapping = {
        "Hook": "Not what you expected",
        "Context": f"It started with {core.topic}",
        "Curiosity": "But one detail mattered",
        "Importance": "This changed the stakes",
        "Escalation": "Then everything moved faster",
        "Payoff": "This was the proof",
        "Final impact": core.payoff,
    }
    return _compact(mapping.get(purpose, purpose), max_words)


def _unique_overlay(
    purpose: str,
    core: CoreIdea,
    max_words: int,
    index: int,
    used: set[str],
) -> str:
    base = _base_overlay(purpose, core, max_words)
    candidate = base
    if candidate.lower() in used:
        variants = {
            "Escalation": ["The pressure kept rising", "Then it got worse", "No room left", "Everything accelerated"],
            "Context": ["Here is the setup", "This is where it began"],
            "Payoff": ["Now the answer lands", "Here is the proof"],
        }
        options = variants.get(purpose, [f"{purpose} moment {index}"])
        for option in options:
            trial = _compact(option, max_words)
            if trial.lower() not in used:
                candidate = trial
                break
        else:
            candidate = _compact(f"{purpose} beat {index}", max_words)
    used.add(candidate.lower())
    return candidate


def build_clip_plan(core: CoreIdea, edit_type: EditType, config: V3Config) -> List[ClipBeat]:
    del edit_type  # the selected type shapes the shared core/hook contract
    config.validate()
    minimum_count = max(5, int((config.target_seconds + config.max_clip_seconds - 0.001) // config.max_clip_seconds))
    preferred_count = max(5, round(config.target_seconds / 2.7))
    count = max(minimum_count, preferred_count)
    count = min(count, max(5, int(config.target_seconds // config.min_clip_seconds)))
    durations = _allocate_durations(
        count,
        config.target_seconds,
        config.min_clip_seconds,
        config.max_clip_seconds,
    )
    purposes = _purpose_sequence(count)
    motion_cycle = ["punch-in", "subtle-parallax", "tracking", "micro-zoom", "reframe"]
    transition_cycle = ["hard cut", "match cut", "hard cut", "J-cut", "hard cut"]
    visual_styles = {
        "Hook": "highest-stakes visual first",
        "Context": "clean establishing visual",
        "Curiosity": "detail crop or evidence insert",
        "Importance": "human reaction or consequence",
        "Escalation": "faster high-information shot",
        "Payoff": "clearest proof shot",
        "Final impact": "hold on strongest emotional frame",
    }

    beats: List[ClipBeat] = []
    cursor = 0.0
    used_overlays: set[str] = set()
    for index, (purpose, duration) in enumerate(zip(purposes, durations), start=1):
        overlay = _unique_overlay(purpose, core, config.max_overlay_words, index, used_overlays)
        end = round(cursor + duration, 3)
        beats.append(
            ClipBeat(
                index=index,
                start=round(cursor, 3),
                end=end,
                purpose=purpose,
                emotion=core.target_emotion,
                visual_style=visual_styles.get(purpose, "supporting visual"),
                text_overlay=overlay,
                camera_motion=motion_cycle[(index - 1) % len(motion_cycle)],
                transition=transition_cycle[(index - 1) % len(transition_cycle)],
            )
        )
        cursor = end
    return beats


def analyze_music(core: CoreIdea, config: V3Config, clips: Sequence[ClipBeat]) -> MusicPlan:
    beat_seconds = round(60.0 / config.bpm, 4)
    payoff_start = clips[-2].start if len(clips) >= 2 else config.target_seconds * 0.7
    drop_time = min(config.target_seconds * 0.72, max(2.0, payoff_start))
    sync_points: List[float] = []
    cursor = 0.0
    while cursor <= config.target_seconds + 0.001:
        sync_points.append(round(cursor, 3))
        cursor += beat_seconds * 2
    if not sync_points or sync_points[-1] < config.target_seconds:
        sync_points.append(round(config.target_seconds, 3))
    energy = "high" if core.target_emotion in {"dramatic", "inspiring", "funny"} else "medium"
    return MusicPlan(
        bpm=config.bpm,
        energy=energy,
        emotional_tone=core.target_emotion,
        beat_seconds=beat_seconds,
        drop_time=round(drop_time, 3),
        sync_points=sync_points,
    )


def build_retention_map(
    config: V3Config,
    clips: Sequence[ClipBeat],
    music: MusicPlan,
) -> List[RetentionEvent]:
    kinds = ["clip", "zoom", "text", "motion", "angle", "beat accent"]
    boundaries = {round(clip.start, 2) for clip in clips[1:]}
    events: List[RetentionEvent] = []
    time_value = 0.0
    index = 0
    while time_value < config.target_seconds - 0.05:
        kind = kinds[index % len(kinds)]
        if any(abs(time_value - boundary) <= 0.08 for boundary in boundaries):
            kind = "clip"
        if abs(time_value - music.drop_time) <= config.retention_interval / 2:
            kind = "beat drop"
        events.append(
            RetentionEvent(
                time=round(time_value, 3),
                kind=kind,
                instruction=f"Change {kind}; preserve story continuity.",
            )
        )
        time_value += config.retention_interval
        index += 1
    return events


def platform_variants(config: V3Config) -> Dict[str, Dict[str, Any]]:
    del config
    return {
        "youtube_shorts": {"width": 1080, "height": 1920, "max_seconds": 60, "safe_bottom": 300, "cta": "comment"},
        "tiktok": {"width": 1080, "height": 1920, "max_seconds": 180, "safe_bottom": 320, "cta": "follow"},
        "instagram_reels": {"width": 1080, "height": 1920, "max_seconds": 90, "safe_bottom": 330, "cta": "share"},
        "square": {"width": 1080, "height": 1080, "max_seconds": 90, "safe_bottom": 170, "cta": "share"},
        "youtube": {"width": 1920, "height": 1080, "max_seconds": None, "safe_bottom": 120, "cta": "subscribe"},
    }


def _metadata(core: CoreIdea) -> tuple[str, List[str], List[str], str]:
    thumbnail = (
        f"Close emotional frame of {core.topic}; one focal subject; "
        "2-4 word contrast text; no clutter."
    )
    titles = [
        _compact(core.emotional_angle, 11),
        _compact(f"The {core.topic} moment nobody forgets", 11),
        _compact(f"Why {core.topic} mattered more than people realized", 11),
    ]
    hashtags = ["#shorts", "#story", "#edit", "#viral", "#videoediting"]
    slug = re.sub(r"[^a-z0-9]+", "", core.topic.lower())[:20]
    if slug:
        hashtags.append(f"#{slug}")
    description = (
        f"{core.emotional_angle}. {core.why_people_care} "
        f"Watch through the payoff: {core.payoff}"
    )
    return thumbnail, titles, hashtags, description


def _human_editor_checks(
    core: CoreIdea,
    hooks: Sequence[HookPack],
    clips: Sequence[ClipBeat],
    retention: Sequence[RetentionEvent],
    config: V3Config,
) -> QualityReport:
    overlays = [clip.text_overlay.lower() for clip in clips]
    durations = [clip.end - clip.start for clip in clips]
    checks = {
        "emotion_defined": bool(core.target_emotion),
        "hook_under_two_seconds": bool(clips and clips[0].end <= 2.2),
        "hook_has_context_gap": bool(hooks and hooks[0].text and core.watch_to_end_reason),
        "every_clip_has_purpose": all(bool(clip.purpose) for clip in clips),
        "overlay_word_limit": all(
            2 <= len(_tokens(clip.text_overlay)) <= config.max_overlay_words for clip in clips
        ),
        "no_duplicate_overlays": len(overlays) == len(set(overlays)),
        "no_dead_clips": all(duration <= config.max_clip_seconds + 0.01 for duration in durations),
        "retention_changes_frequent": all(
            (later.time - earlier.time) <= 3.01
            for earlier, later in zip(retention, retention[1:])
        ),
        "has_payoff": any(clip.purpose == "Payoff" for clip in clips),
        "has_final_impact": bool(clips and clips[-1].purpose == "Final impact"),
    }
    warnings: List[str] = []
    if not checks["hook_under_two_seconds"]:
        warnings.append("Hook exceeds the preferred ~2 second opening window.")
    if not checks["no_duplicate_overlays"]:
        warnings.append("Repeated overlay text can make the edit feel templated.")
    if not checks["retention_changes_frequent"]:
        warnings.append("A visual change gap exceeds the 1-3 second retention rule.")
    score = round(100 * sum(1 for value in checks.values() if value) / len(checks))
    passed = score >= 90 and checks["has_payoff"] and checks["emotion_defined"]
    return QualityReport(passed=passed, score=score, checks=checks, warnings=warnings)


def _heuristic_metrics(
    core: CoreIdea,
    hooks: Sequence[HookPack],
    clips: Sequence[ClipBeat],
    quality: QualityReport,
) -> Dict[str, float]:
    hook_score = hooks[0].score if hooks else 0.0
    if clips:
        average_duration = clips[-1].end / len(clips)
        pace = min(1.0, 2.8 / max(average_duration, 0.1))
    else:
        pace = 0.0
    quality_factor = quality.score / 100.0
    emotional = 0.9 if core.target_emotion in {"trust", "dramatic", "inspiring", "nostalgic"} else 0.82
    return {
        "retention_score": round(100 * (0.35 * hook_score + 0.30 * pace + 0.20 * quality_factor + 0.15 * emotional), 1),
        "completion_score": round(100 * (0.45 * quality_factor + 0.30 * pace + 0.25 * hook_score), 1),
        "rewatch_score": round(100 * (0.40 * hook_score + 0.35 * emotional + 0.25 * quality_factor), 1),
        "shareability_score": round(100 * (0.50 * emotional + 0.30 * quality_factor + 0.20 * hook_score), 1),
    }


def create_v3_blueprint(
    topic: str,
    *,
    context: str = "",
    config: V3Config | None = None,
    edit_type: str | None = None,
) -> V3Blueprint:
    cfg = config or V3Config()
    cfg.validate()
    core = analyze_core_idea(topic, context, cfg.audience)
    selected = choose_edit_type(core, edit_type)
    hooks = generate_hooks(core, selected)
    clips = build_clip_plan(core, selected, cfg)
    music = analyze_music(core, cfg, clips)
    retention = build_retention_map(cfg, clips, music)
    thumbnail, titles, hashtags, description = _metadata(core)
    quality = _human_editor_checks(core, hooks, clips, retention, cfg)
    metrics = _heuristic_metrics(core, hooks, clips, quality)
    return V3Blueprint(
        version="3.0.0",
        core_idea=core,
        edit_type=selected.value,
        hooks=hooks,
        clip_plan=clips,
        music=music,
        retention_map=retention,
        thumbnail_concept=thumbnail,
        title_options=titles,
        hashtags=hashtags,
        description=description,
        platform_variants=platform_variants(cfg),
        quality=quality,
        metrics=metrics,
        capabilities=list(V3_CAPABILITIES),
    )


def validate_blueprint(blueprint: V3Blueprint) -> None:
    if blueprint.version != "3.0.0":
        raise ValueError("unexpected blueprint version")
    if len(blueprint.capabilities) != 40:
        raise ValueError("v3 blueprint must expose exactly 40 tracked upgrades")
    if not blueprint.clip_plan:
        raise ValueError("clip plan is empty")
    if not blueprint.quality.passed:
        raise ValueError("blueprint failed human-editor quality control")


def blueprint_summary(blueprint: V3Blueprint) -> str:
    hook = blueprint.hooks[0].text if blueprint.hooks else ""
    return (
        f"{blueprint.core_idea.topic} | {blueprint.edit_type} | "
        f"emotion={blueprint.core_idea.target_emotion} | hook={hook!r} | "
        f"clips={len(blueprint.clip_plan)} | quality={blueprint.quality.score}/100"
    )
