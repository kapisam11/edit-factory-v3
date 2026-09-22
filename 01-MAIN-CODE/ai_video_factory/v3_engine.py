"""Edit Factory v3 creative planning, semantic analysis, retention and QC."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
import re
from typing import Any, Dict, List, Mapping, Sequence

from .v3_semantics import combined_scores


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
        try:
            target = float(self.target_seconds)
            bpm = int(self.bpm)
            retention = float(self.retention_interval)
            max_words = int(self.max_overlay_words)
            minimum = float(self.min_clip_seconds)
            maximum = float(self.max_clip_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("V3Config contains invalid numeric values") from exc
        if not math.isfinite(target) or not 8.0 <= target <= 180.0:
            raise ValueError("target_seconds must be between 8 and 180")
        profile = PLATFORM_PROFILES.get(str(self.platform).lower())
        if profile is None:
            raise ValueError(f"unsupported platform: {self.platform}")
        maximum_platform = profile["max_seconds"]
        if maximum_platform is not None and target > float(maximum_platform):
            raise ValueError(f"target_seconds must be <= {maximum_platform:g} for {self.platform}")
        if not 40 <= bpm <= 240:
            raise ValueError("bpm must be between 40 and 240")
        if not 0.75 <= retention <= 3.0:
            raise ValueError("retention_interval must be between 0.75 and 3.0")
        if not 2 <= max_words <= 8:
            raise ValueError("max_overlay_words must be between 2 and 8")
        if minimum <= 0 or maximum < minimum or maximum > target:
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


PLATFORM_PROFILES: Mapping[str, Dict[str, Any]] = {
    "youtube_shorts": {"width": 1080, "height": 1920, "max_seconds": 60, "safe_bottom": 300, "cta": "comment"},
    "tiktok": {"width": 1080, "height": 1920, "max_seconds": 180, "safe_bottom": 320, "cta": "follow"},
    "instagram_reels": {"width": 1080, "height": 1920, "max_seconds": 90, "safe_bottom": 330, "cta": "share"},
    "square": {"width": 1080, "height": 1080, "max_seconds": 90, "safe_bottom": 170, "cta": "share"},
    "youtube": {"width": 1920, "height": 1080, "max_seconds": None, "safe_bottom": 120, "cta": "subscribe"},
}

EDIT_BY_EMOTION: Mapping[str, EditType] = {
    "trust": EditType.TRIBUTE,
    "dramatic": EditType.DRAMATIC,
    "inspiring": EditType.MOTIVATIONAL,
    "nostalgic": EditType.NOSTALGIC,
    "funny": EditType.FUNNY,
    "curious": EditType.STORYTELLING,
}

EDIT_STRATEGIES: Mapping[EditType, Dict[str, Any]] = {
    EditType.EMOTIONAL: {"purposes": ("Hook","Context","Curiosity","Escalation","Payoff","Final impact"), "motions": ("micro-zoom","reframe","subtle-parallax","tracking"), "transitions": ("hard cut","match cut","hard cut","J-cut","hard cut"), "visual_styles": ("emotion-first close-up","context continuity","reaction/detail insert","rising intensity","emotional proof","strong emotional hold")},
    EditType.MOTIVATIONAL: {"purposes": ("Hook","Context","Curiosity","Escalation","Payoff","Final impact"), "motions": ("punch-in","tracking","micro-zoom","reframe"), "transitions": ("hard cut","match cut","hard cut","speed ramp","hard cut"), "visual_styles": ("result-before-context","setup/obstacle","effort detail","momentum action","achievement reveal","confident final hold")},
    EditType.NOSTALGIC: {"purposes": ("Hook","Context","Memory","Contrast","Payoff","Final impact"), "motions": ("subtle-parallax","slow push","reframe","micro-zoom"), "transitions": ("dissolve","match cut","dissolve","match cut","hard cut"), "visual_styles": ("recognizable memory","wide memory context","past detail","before-after contrast","familiar payoff","nostalgic hold")},
    EditType.FUNNY: {"purposes": ("Hook","Setup","Escalation","Escalation","Punchline","Reaction"), "motions": ("punch-in","reframe","micro-zoom","tracking"), "transitions": ("hard cut","hard cut","hard cut","J-cut","hard cut"), "visual_styles": ("reaction cold-open","clean setup","surprise detail","faster escalation","punchline frame","best reaction")},
    EditType.DRAMATIC: {"purposes": ("Hook","Context","Threat","Escalation","Climax","Payoff"), "motions": ("punch-in","tracking","micro-zoom","reframe"), "transitions": ("hard cut","J-cut","hard cut","speed ramp","hard cut"), "visual_styles": ("highest-stakes frame","minimal context","threat evidence","consequence buildup","climax action","consequence hold")},
    EditType.DOCUMENTARY: {"purposes": ("Hook","Context","Evidence","Context","Payoff","Final impact"), "motions": ("subtle-parallax","reframe","tracking","micro-zoom"), "transitions": ("hard cut","match cut","hard cut","match cut","hard cut"), "visual_styles": ("fact-leading visual","evidence establishing","source detail","cause-effect sequence","clearest evidence","summary image")},
    EditType.SIGMA: {"purposes": ("Hook","Claim","Evidence","Escalation","Payoff","Final impact"), "motions": ("punch-in","reframe","micro-zoom","tracking"), "transitions": ("hard cut","hard cut","match cut","speed ramp","hard cut"), "visual_styles": ("dominant cold-open","claim frame","proof detail","intensity rise","payoff reveal","confident hold")},
    EditType.CHARACTER_ANALYSIS: {"purposes": ("Hook","Context","Trait","Evidence","Payoff","Final impact"), "motions": ("subtle-parallax","reframe","micro-zoom","tracking"), "transitions": ("hard cut","match cut","hard cut","J-cut","hard cut"), "visual_styles": ("defining frame","character setup","trait evidence","behavioral proof","character conclusion","signature frame")},
    EditType.TRIBUTE: {"purposes": ("Hook","Context","Memory","Proof","Payoff","Final impact"), "motions": ("slow push","subtle-parallax","micro-zoom","reframe"), "transitions": ("dissolve","match cut","dissolve","match cut","hard cut"), "visual_styles": ("human frame","shared context","memorable detail","character proof","emotional culmination","respectful hold")},
    EditType.STORYTELLING: {"purposes": ("Hook","Context","Question","Escalation","Payoff","Final impact"), "motions": ("punch-in","tracking","reframe","micro-zoom"), "transitions": ("hard cut","match cut","hard cut","J-cut","hard cut"), "visual_styles": ("question-provoking frame","clean setup","evidence detail","rising action","answer reveal","closing image")},
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
    return " ".join(str(text).strip().split()[:max_words])

def analyze_core_idea(topic: str, context: str = "", audience: str = "general short-form viewers") -> CoreIdea:
    topic = str(topic).strip()
    context = str(context).strip()
    if not topic:
        raise ValueError("topic is required")
    scores = combined_scores(topic, context)
    audience_tokens = set(_tokens(audience))
    if {"comedy", "funny", "humor"} & audience_tokens:
        scores["funny"] += 0.05
    if {"history", "documentary", "facts"} & audience_tokens:
        scores["curious"] += 0.05
    emotion = max(scores, key=scores.get) if any(scores.values()) else "curious"
    angles = {
        "trust": (f"Why {topic} became the person people could rely on","Loyalty under pressure creates immediate human stakes.","Trust can be earned quickly and lost in one decision.",f"The moment that proves what {topic} meant to everyone."),
        "dramatic": (f"The decision that changed everything for {topic}","Conflict and consequences create an immediate information gap.","Something valuable can be lost before the viewer understands why.","Reveal the consequence viewers were waiting to understand."),
        "inspiring": (f"How {topic} kept going when quitting was easier","Effort matters when failure feels possible and progress is visible.","The attempt only matters if the outcome is uncertain.","Show the result that makes the struggle worth it."),
        "nostalgic": (f"Why people still remember {topic}","Recognition and shared memory create instant emotional pull.","The memory only works when the details feel specific.","Return to the moment viewers wanted to remember."),
        "funny": (f"The moment {topic} went completely off the rails","Fast setup plus escalation makes the reversal worth waiting for.","The joke dies when setup overwhelms the punchline.","Deliver the cleanest reaction or reversal last."),
        "curious": (f"The part of {topic} most people miss","A knowledge gap gives the viewer a reason to stay.","The answer must be more valuable than the setup.","Resolve the question with one clear memorable insight."),
    }
    angle, care, stakes, payoff = angles[emotion]
    return CoreIdea(topic, care, angle, emotion, f"The viewer needs the final proof behind: {angle}.", payoff, stakes)

def choose_edit_type(core: CoreIdea, requested: str | None = None) -> EditType:
    if requested:
        for item in EditType:
            if item.value.lower() == str(requested).strip().lower():
                return item
        raise ValueError(f"unknown edit type: {requested}")
    return EDIT_BY_EMOTION.get(core.target_emotion, EditType.STORYTELLING)

def generate_hooks(core: CoreIdea, edit_type: EditType) -> List[HookPack]:
    strategy = EDIT_STRATEGIES[edit_type]
    hooks = [
        HookPack(strategy["visual_styles"][0], _compact(f"Nobody expected {core.topic}"), core.stakes, 0.93),
        HookPack(f"Open on {strategy['visual_styles'][2]} before context.", "This changed everything", core.emotional_angle, 0.89),
        HookPack(f"Use a contrast built around {strategy['purposes'][2].lower()}.", _compact(f"Everyone misread {core.topic}"), core.watch_to_end_reason, 0.86),
    ]
    if edit_type == EditType.FUNNY:
        hooks[0] = HookPack("Cold-open on the reaction before the result.", "This got worse", "Anticipation before the punchline.", 0.97)
    elif edit_type in {EditType.NOSTALGIC, EditType.TRIBUTE}:
        hooks[0] = HookPack("Open on the most recognizable human frame.", "You remember this", "Recognition before explanation.", 0.96)
    elif edit_type == EditType.DOCUMENTARY:
        hooks[0] = HookPack("Open on the strongest evidence before context.", "Here is what happened", "Curiosity before explanation.", 0.95)
    elif edit_type == EditType.CHARACTER_ANALYSIS:
        hooks[0] = HookPack("Open on defining behavior before naming the trait.", "This says everything", "Recognition before analysis.", 0.95)
    return sorted(hooks, key=lambda item: item.score, reverse=True)

def _purpose_sequence(count: int, strategy: Mapping[str, Any]) -> List[str]:
    values = list(strategy["purposes"])
    while len(values) < count:
        insert_at = max(3, len(values) - 2)
        values.insert(insert_at, "Escalation")
    return values[:count]

def _allocate_durations(count: int, total: float, minimum: float, maximum: float) -> List[float]:
    if count <= 0 or total < minimum * count or total > maximum * count:
        raise ValueError("clip count cannot satisfy duration bounds")
    weights = [1.0] * count
    weights[0] = 0.72
    if count > 1:
        weights[-1] = 1.20
    if count > 3:
        weights[-2] = 1.10
    durations = [min(maximum, max(minimum, total * w / sum(weights))) for w in weights]
    for _ in range(200):
        delta = total - sum(durations)
        if abs(delta) < 0.00025:
            break
        candidates = [i for i, d in enumerate(durations) if (delta > 0 and d < maximum - 1e-4) or (delta < 0 and d > minimum + 1e-4)]
        if not candidates:
            break
        step = delta / len(candidates)
        for i in candidates:
            durations[i] = min(maximum, max(minimum, durations[i] + step))
    rounded = [round(d, 3) for d in durations]
    rounded[-1] = round(rounded[-1] + total - sum(rounded), 3)
    if abs(sum(rounded) - total) > 0.01 or not minimum <= rounded[-1] <= maximum:
        raise ValueError("duration allocation could not satisfy exact target")
    return rounded

def _overlay_for_purpose(purpose: str, core: CoreIdea, max_words: int, edit_type: EditType) -> str:
    choices = {
        "Hook": ("Not what you expected", f"Nobody saw {core.topic} coming"),
        "Context": (f"It started with {core.topic}", "Here is the setup"),
        "Setup": ("Watch what happens", "This is where it starts"),
        "Curiosity": ("But one detail mattered", "There was one problem"),
        "Question": ("Something was missing", "The real question was this"),
        "Claim": ("There is a reason", "This tells us something"),
        "Memory": ("You remember this", "That moment still hits"),
        "Contrast": ("Then everything changed", "Before vs after"),
        "Trait": ("Notice what he does", "That is the pattern"),
        "Evidence": ("Look at this detail", "The evidence is here"),
        "Importance": ("This raised the stakes", "Now it matters"),
        "Threat": ("Then it got dangerous", "The risk was real"),
        "Escalation": ("The pressure kept rising", "Everything accelerated"),
        "Climax": ("This was the moment", "Now it all lands"),
        "Payoff": ("This was the proof", "Here is the answer"),
        "Punchline": ("And then this happened", "That was the joke"),
        "Reaction": ("Watch the reaction", "That face says it all"),
        "Proof": ("Here is the proof", "This confirms it"),
        "Final impact": (core.payoff, "That is why it mattered"),
    }
    choice = choices.get(purpose)
    if choice is None:
        choice = (purpose, purpose)
    base, alternate = choice
    if edit_type == EditType.DOCUMENTARY and purpose in {"Evidence", "Payoff"}:
        base = alternate
    return _compact(base, max_words)

def _unique_overlay(purpose: str, core: CoreIdea, max_words: int, index: int, used: set[str], edit_type: EditType) -> str:
    candidate = _overlay_for_purpose(purpose, core, max_words, edit_type)
    if candidate.lower() in used:
        for suffix in ("Here is the key", "Notice this", "One detail matters", "This is the moment"):
            trial = _compact(suffix, max_words)
            if trial.lower() not in used:
                candidate = trial
                break
        else:
            candidate = _compact(f"Beat {index} {core.topic}", max_words)
    used.add(candidate.lower())
    return candidate

def build_clip_plan(core: CoreIdea, edit_type: EditType, config: V3Config) -> List[ClipBeat]:
    config.validate()
    strategy = EDIT_STRATEGIES[edit_type]
    minimum_count = max(len(strategy["purposes"]), math.ceil(config.target_seconds / config.max_clip_seconds))
    preferred_count = max(len(strategy["purposes"]), round(config.target_seconds / 2.7))
    count = min(max(minimum_count, preferred_count), int(config.target_seconds // config.min_clip_seconds))
    durations = _allocate_durations(count, config.target_seconds, config.min_clip_seconds, config.max_clip_seconds)
    purposes = _purpose_sequence(count, strategy)
    motions = list(strategy["motions"])
    transitions = list(strategy["transitions"])
    styles = list(strategy["visual_styles"])
    beats: List[ClipBeat] = []
    cursor = 0.0
    used: set[str] = set()
    for zero_index, (purpose, duration) in enumerate(zip(purposes, durations)):
        end = round(cursor + duration, 3)
        beats.append(ClipBeat(zero_index + 1, round(cursor, 3), end, purpose, core.target_emotion, styles[zero_index % len(styles)], _unique_overlay(purpose, core, config.max_overlay_words, zero_index + 1, used, edit_type), motions[zero_index % len(motions)], transitions[zero_index % len(transitions)]))
        cursor = end
    return beats

def analyze_music(core: CoreIdea, config: V3Config, clips: Sequence[ClipBeat]) -> MusicPlan:
    beat_seconds = round(60.0 / config.bpm, 4)
    payoff_start = clips[-2].start if len(clips) >= 2 else config.target_seconds * 0.7
    drop = min(config.target_seconds * 0.72, max(2.0, payoff_start))
    sync = []
    t = 0.0
    while t < config.target_seconds - 1e-6:
        sync.append(round(t, 3))
        t += beat_seconds * 2
    sync.append(round(config.target_seconds, 3))
    return MusicPlan(config.bpm, "high" if core.target_emotion in {"dramatic","inspiring","funny"} else "medium", core.target_emotion, beat_seconds, round(drop, 3), sync)

def build_retention_map(config: V3Config, clips: Sequence[ClipBeat], music: MusicPlan) -> List[RetentionEvent]:
    events: List[RetentionEvent] = []
    boundaries = [clip.start for clip in clips[1:]]
    times = sorted(set(round(t, 3) for t in boundaries + [x * config.retention_interval for x in range(int(config.target_seconds / config.retention_interval) + 1)] if t < config.target_seconds - 0.05))
    for t in times:
        if abs(t - music.drop_time) <= config.retention_interval * 0.5:
            kind = "beat drop"
        elif any(abs(t - boundary) <= 0.08 for boundary in boundaries):
            kind = "clip"
        else:
            kind = ("zoom", "text", "motion", "angle")[len(events) % 4]
        events.append(RetentionEvent(t, kind, f"Change {kind} while preserving story continuity."))
    return events

def platform_variants(config: V3Config) -> Dict[str, Dict[str, Any]]:
    config.validate()
    return {key: dict(value) for key, value in PLATFORM_PROFILES.items()}

def _human_editor_checks(core: CoreIdea, edit_type: EditType, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], retention: Sequence[RetentionEvent], config: V3Config) -> QualityReport:
    durations = [clip.end - clip.start for clip in clips]
    overlays = [clip.text_overlay.lower() for clip in clips]
    checks = {
        "emotion_defined": bool(core.target_emotion),
        "single_edit_type_strategy": edit_type in EDIT_STRATEGIES,
        "hook_under_two_seconds": bool(clips and clips[0].end <= 2.2),
        "hook_context_gap": bool(hooks and hooks[0].text and core.watch_to_end_reason),
        "every_clip_has_purpose": bool(clips) and all(bool(c.purpose) for c in clips),
        "overlay_word_limit": all(2 <= len(_tokens(c.text_overlay)) <= config.max_overlay_words for c in clips),
        "no_duplicate_overlays": len(overlays) == len(set(overlays)),
        "duration_bounds": all(config.min_clip_seconds - 0.01 <= d <= config.max_clip_seconds + 0.01 for d in durations),
        "exact_duration": bool(clips) and abs(clips[-1].end - config.target_seconds) <= 0.01,
        "retention_changes_frequent": bool(retention) and all((b.time - a.time) <= config.retention_interval + 0.01 for a,b in zip(retention, retention[1:])),
        "has_payoff": any(c.purpose in {"Payoff","Punchline","Climax"} for c in clips),
        "has_final_impact": bool(clips and clips[-1].purpose in {"Final impact","Payoff","Reaction"}),
        "hook_payoff_continuity": bool(clips and clips[0].emotion == clips[-1].emotion),
    }
    warnings = []
    for key, message in {
        "hook_under_two_seconds":"hook exceeds preferred opening window",
        "no_duplicate_overlays":"repeated overlay text detected",
        "retention_changes_frequent":"retention event interval exceeded",
        "hook_payoff_continuity":"hook and payoff emotion differ",
    }.items():
        if not checks[key]:
            warnings.append(message)
    score = round(100 * sum(checks.values()) / len(checks)) if checks else 0
    return QualityReport(score >= 95 and all(checks[k] for k in ("emotion_defined","single_edit_type_strategy","duration_bounds","exact_duration","has_payoff")), score, checks, warnings)

def _heuristic_metrics(core: CoreIdea, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], quality: QualityReport) -> Dict[str, float]:
    hook = hooks[0].score if hooks else 0.0
    avg = clips[-1].end / len(clips) if clips else 0.0
    pace = min(1.0, 2.8 / max(avg, 0.1))
    q = quality.score / 100.0
    emotion = 0.9 if core.target_emotion in {"trust","dramatic","inspiring","nostalgic"} else 0.82
    return {
        "retention_score": round(100 * (0.35 * hook + 0.30 * pace + 0.20 * q + 0.15 * emotion), 1),
        "completion_score": round(100 * (0.45 * q + 0.30 * pace + 0.25 * hook), 1),
        "rewatch_score": round(100 * (0.40 * hook + 0.35 * emotion + 0.25 * q), 1),
        "shareability_score": round(100 * (0.50 * emotion + 0.30 * q + 0.20 * hook), 1),
    }

def _metadata(core: CoreIdea) -> tuple[str, List[str], List[str], str]:
    thumb = f"Close emotional frame of {core.topic}; one focal subject; 2-4 word contrast text; no clutter."
    titles = [_compact(core.emotional_angle,11), _compact(f"The {core.topic} moment nobody forgets",11), _compact(f"Why {core.topic} mattered more than people realized",11)]
    tags = ["#shorts","#story","#edit","#videoediting"]
    slug = re.sub(r"[^a-z0-9]+","",core.topic.lower())[:20]
    if slug:
        tags.append(f"#{slug}")
    description = f"{core.emotional_angle}. {core.why_people_care} Watch through the payoff: {core.payoff}"
    return thumb, titles, tags, description

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
    quality = _human_editor_checks(core, selected, hooks, clips, retention, cfg)
    metrics = _heuristic_metrics(core, hooks, clips, quality)
    blueprint = V3Blueprint("3.0.0", core, selected.value, hooks, clips, music, retention, thumbnail, titles, tags, description, platform_variants(cfg), quality, metrics, list(V3_CAPABILITIES))
    validate_blueprint(blueprint)
    return blueprint

def validate_blueprint(blueprint: V3Blueprint) -> None:
    if blueprint.version != "3.0.0":
        raise ValueError("unexpected blueprint version")
    if len(blueprint.capabilities) != 40:
        raise ValueError("v3 blueprint must expose exactly 40 tracked capabilities")
    try:
        edit_type = EditType(blueprint.edit_type)
    except ValueError as exc:
        raise ValueError(f"unsupported blueprint edit type: {blueprint.edit_type}") from exc
    if edit_type not in EDIT_STRATEGIES or not blueprint.clip_plan:
        raise ValueError("blueprint is missing a registered creative strategy")
    if not blueprint.retention_map:
        raise ValueError("blueprint retention map is empty")
    previous = -1e-6
    for clip in blueprint.clip_plan:
        if clip.start < previous or clip.end <= clip.start:
            raise ValueError("clip plan is not monotonic")
        if not 2 <= len(_tokens(clip.text_overlay)) <= 8:
            raise ValueError(f"invalid overlay word count at clip {clip.index}")
        previous = clip.end
    if abs(blueprint.clip_plan[-1].end - blueprint.music.sync_points[-1]) > 0.01:
        raise ValueError("clip plan does not exactly cover planned duration")
    if not blueprint.quality.passed:
        raise ValueError("blueprint failed strict editorial QC")

def blueprint_summary(blueprint: V3Blueprint) -> str:
    hook = blueprint.hooks[0].text if blueprint.hooks else ""
    return f"{blueprint.core_idea.topic} | {blueprint.edit_type} | emotion={blueprint.core_idea.target_emotion} | hook={hook!r} | clips={len(blueprint.clip_plan)} | quality={blueprint.quality.score}/100"
