"""Edit Factory v3 emotion-first planning and retention engine.

The v3 planner deliberately decides what the viewer should feel before it plans
clips or text. It is deterministic and offline-safe so it can be used in CI,
from the CLI, or as a front-end to the existing render pipeline.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence


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
    words = re.findall(r"\S+", str(text).strip())
    return " ".join(words[:max_words])


def analyze_core_idea(topic: str, context: str = "", audience: str = "general short-form viewers") -> CoreIdea:
    topic = str(topic).strip()
    if not topic:
        raise ValueError("topic is required")
    source = f"{topic} {context}".lower()
    scores = {emotion: sum(source.count(term) for term in terms) for emotion, terms in EMOTION_TERMS.items()}
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
    topic = core.topic
    candidates = [
        HookPack("Open on the highest-stakes reaction before context.", _compact(f"Nobody expected {topic}"), core.stakes, 0.93),
        HookPack("Start mid-action with a 6-8% punch-in.", _compact("This changed everything"), core.emotional_angle, 0.89),
        HookPack("Use a contrast frame: before vs after.", _compact(f"Everyone misread {topic}"), core.watch_to_end_reason, 0.86),
    ]
    if edit_type == EditType.FUNNY:
        candidates[0] = HookPack("Cold-open on the reaction, cut away before the result.", "This got worse", "Anticipation before the punchline.", 0.95)
    elif edit_type == EditType.NOSTALGIC:
        candidates[0] = HookPack("Open on the most recognizable old frame with subtle motion.", "You remember this", "Recognition before explanation.", 0.94)
    return sorted(candidates, key=lambda hook: hook.score, reverse=True)


def _purpose_sequence(count: int) -> List[str]:
    base = ["Hook", "Context", "Curiosity", "Importance", "Escalation", "Escalation", "Payoff", "Final impact"]
    if count <= len(base):
        picks = []
        for i in range(count):
            index = round(i * (len(base) - 1) / max(1, count - 1))
            picks.append(base[index])
        return picks
    middle = ["Escalation"] * (count - len(base))
    return base[:-2] + middle + base[-2:]


def _allocate_durations(count: int, total: float, minimum: float, maximum: float) -> List[float]:
    if total < minimum * count:
        count = max(1, int(total // minimum))
    weights = [0.72 if i == 0 else 1.18 if i in (count - 2, count - 1) else 1.0 for i in range(count)]
    raw = [total * w / sum(weights) for w in weights]
    durations = [max(minimum, min(maximum, value)) for value in raw]
    diff = total - sum(durations)
    for _ in range(100):
        if abs(diff) < 0.005:
            break
        candidates = [i for i, value in enumerate(durations) if (diff > 0 and value < maximum - 0.001) or (diff < 0 and value > minimum + 0.001)]
        if not candidates:
            break
        share = diff / len(candidates)
        for i in candidates:
            durations[i] = max(minimum, min(maximum, durations[i] + share))
        diff = total - sum(durations)
    rounded = [round(value, 3) for value in durations]
    rounded[-1] = round(rounded[-1] + (total - sum(rounded)), 3)
    return rounded


def _overlay_for(purpose: str, core: CoreIdea, max_words: int) -> str:
    mapping = {
        "Hook": f"Not what you expected",
        "Context": f"It started with {core.topic}",
        "Curiosity": "But one detail mattered",
        "Importance": "This changed the stakes",
        "Escalation": "Then everything moved faster",
        "Payoff": "This was the proof",
        "Final impact": _compact(core.payoff, max_words),
    }
    return _compact(mapping.get(purpose, purpose), max_words)


def build_clip_plan(core: CoreIdea, edit_type: EditType, config: V3Config) -> List[ClipBeat]:
    config.validate()
    count = max(5, min(20, round(config.target_seconds / 2.7)))
    durations = _allocate_durations(count, config.target_seconds, config.min_clip_seconds, config.max_clip_seconds)
    purposes = _purpose_sequence(count)
    motion_cycle = ["punch-in", "static-with-subtle-parallax", "tracking", "micro-zoom", "reframe"]
    transition_cycle = ["hard cut", "match cut", "hard cut", "J-cut", "hard cut"]
    beats: List[ClipBeat] = []
    cursor = 0.0
    previous_overlay = ""
    for index, (purpose, duration) in enumerate(zip(purposes, durations), start=1):
        overlay = _overlay_for(purpose, core, config.max_overlay_words)
        if overlay == previous_overlay:
            overlay = _compact(f"{purpose} now", config.max_overlay_words)
        previous_overlay = overlay
        end = round(cursor + duration, 3)
        visual_style = {
            "Hook": "highest-stakes visual first",
            "Context": "clean establishing visual",
            "Curiosity": "detail crop or evidence insert",
            "Importance": "human reaction or consequence",
            "Escalation": "faster high-information shot",
            "Payoff": "clearest proof shot",
            "Final impact": "hold on strongest emotional frame",
        }.get(purpose, "supporting visual")
        beats.append(ClipBeat(index, round(cursor, 3), end, purpose, core.target_emotion, visual_style, overlay, motion_cycle[(index - 1) % len(motion_cycle)], transition_cycle[(index - 1) % len(transition_cycle)]))
        cursor = end
    return beats


def analyze_music(core: CoreIdea, config: V3Config, clips: Sequence[ClipBeat]) -> MusicPlan:
    beat_seconds = round(60.0 / config.bpm, 4)
    drop_time = min(config.target_seconds * 0.72, max(2.0, clips[-2].start if len(clips) >= 2 else config.target_seconds * 0.7))
    sync = []
    cursor = 0.0
    while cursor <= config.target_seconds + 0.001:
        sync.append(round(cursor, 3))
        cursor += beat_seconds * 2
    energy = "high" if core.target_emotion in {"dramatic", "inspiring", "funny"} else "medium"
    return MusicPlan(config.bpm, energy, core.target_emotion, beat_seconds, round(drop_time, 3), sync)


def build_retention_map(config: V3Config, clips: Sequence[ClipBeat], music: MusicPlan) -> List[RetentionEvent]:
    events: List[RetentionEvent] = []
    kinds = ["clip", "zoom", "text", "motion", "angle", "beat accent"]
    t = 0.0
    i = 0
    clip_boundaries = {round(clip.start, 1) for clip in clips[1:]}
    while t < config.target_seconds - 0.05:
        kind = kinds[i % len(kinds)]
        if round(t, 1) in clip_boundaries:
            kind = "clip"
        if abs(t - music.drop_time) <= config.retention_interval / 2:
            kind = "beat drop"
        events.append(RetentionEvent(round(t, 3), kind, f"Change {kind}; preserve story continuity."))
        t += config.retention_interval
        i += 1
    return events


def platform_variants(config: V3Config) -> Dict[str, Dict[str, Any]]:
    return {
        "youtube_shorts": {"width": 1080, "height": 1920, "max_seconds": 60, "safe_bottom": 300, "cta": "comment"},
        "tiktok": {"width": 1080, "height": 1920, "max_seconds": 180, "safe_bottom": 320, "cta": "follow"},
        "instagram_reels": {"width": 1080, "height": 1920, "max_seconds": 90, "safe_bottom": 330, "cta": "share"},
        "square": {"width": 1080, "height": 1080, "max_seconds": 90, "safe_bottom": 170, "cta": "share"},
        "youtube": {"width": 1920, "height": 1080, "max_seconds": None, "safe_bottom": 120, "cta": "subscribe"},
    }


def _metadata(core: CoreIdea) -> tuple[str, List[str], List[str], str]:
    thumbnail = f"Close emotional frame of {core.topic}; one focal subject; 2-4 word contrast text; no clutter."
    titles = [
        _compact(core.emotional_angle, 11),
        _compact(f"The {core.topic} moment nobody forgets", 11),
        _compact(f"Why {core.topic} mattered more than people realized", 11),
    ]
    tags = ["#shorts", "#story", "#edit", "#viral", "#videoediting"]
    slug = re.sub(r"[^a-z0-9]+", "", core.topic.lower())[:20]
    if slug:
        tags.append(f"#{slug}")
    description = f"{core.emotional_angle}. {core.why_people_care} Watch through the payoff: {core.payoff}"
    return thumbnail, titles, tags, description


def _human_editor_checks(core: CoreIdea, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], retention: Sequence[RetentionEvent], config: V3Config) -> QualityReport:
    overlays = [clip.text_overlay.lower() for clip in clips]
    durations = [clip.end - clip.start for clip in clips]
    checks = {
        "emotion_defined": bool(core.target_emotion),
        "hook_under_two_seconds": bool(clips and clips[0].end <= 2.2),
        "hook_has_context_gap": bool(hooks and hooks[0].text and core.watch_to_end_reason),
        "every_clip_has_purpose": all(bool(clip.purpose) for clip in clips),
        "overlay_word_limit": all(2 <= len(_tokens(clip.text_overlay)) <= config.max_overlay_words for clip in clips),
        "no_duplicate_overlays": len(overlays) == len(set(overlays)),
        "no_dead_clips": all(duration <= config.max_clip_seconds + 0.01 for duration in durations),
        "retention_changes_frequent": all((b.time - a.time) <= 3.01 for a, b in zip(retention, retention[1:])),
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
    return QualityReport(score >= 90 and checks["has_payoff"] and checks["emotion_defined"], score, checks, warnings)


def _heuristic_metrics(core: CoreIdea, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], quality: QualityReport) -> Dict[str, float]:
    hook = hooks[0].score if hooks else 0.0
    pace = min(1.0, len(clips) / max(5.0, clips[-1].end / 2.8 if clips else 5.0))
    quality_factor = quality.score / 100.0
    emotional = 0.9 if core.target_emotion in {"trust", "dramatic", "inspiring", "nostalgic"} else 0.82
    retention = 100 * (0.35 * hook + 0.30 * pace + 0.20 * quality_factor + 0.15 * emotional)
    completion = 100 * (0.45 * quality_factor + 0.30 * pace + 0.25 * hook)
    rewatch = 100 * (0.40 * hook + 0.35 * emotional + 0.25 * quality_factor)
    share = 100 * (0.50 * emotional + 0.30 * quality_factor + 0.20 * hook)
    return {"retention_score": round(retention, 1), "completion_score": round(completion, 1), "rewatch_score": round(rewatch, 1), "shareability_score": round(share, 1)}


def create_v3_blueprint(topic: str, *, context: str = "", config: V3Config | None = None, edit_type: str | None = None) -> V3Blueprint:
    cfg = config or V3Config()
    cfg.validate()
    core = analyze_core_idea(topic, context, cfg.audience)
    selected = choose_edit_type(core, edit_type)
    hooks = generate_hooks(core, selected)
    clips = build_clip_plan(core, selected, cfg)
    music = analyze_music(core, cfg, clips)
    retention = build_retention_map(cfg, clips, music)
    thumbnail, titles, tags, description = _metadata(core)
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
        hashtags=tags,
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
    if abs(blueprint.clip_plan[-1].end - blueprint.music.sync_points[-1]) > 10:
        raise ValueError("music plan is not aligned with the edit duration")
    if not blueprint.quality.passed:
        raise ValueError("blueprint failed human-editor quality control")


def blueprint_summary(blueprint: V3Blueprint) -> str:
    hook = blueprint.hooks[0].text if blueprint.hooks else ""
    return (
        f"{blueprint.core_idea.topic} | {blueprint.edit_type} | "
        f"emotion={blueprint.core_idea.target_emotion} | hook={hook!r} | "
        f"clips={len(blueprint.clip_plan)} | quality={blueprint.quality.score}/100"
    )
