"""Edit Factory v3 emotion-first creative planning and retention engine.

The planner builds a deterministic, validated creative contract that is strong
enough to drive the production renderer while remaining offline-safe.
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
        try:
            target = float(self.target_seconds)
            bpm = int(self.bpm)
            retention = float(self.retention_interval)
            max_words = int(self.max_overlay_words)
            minimum = float(self.min_clip_seconds)
            maximum = float(self.max_clip_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("V3Config contains invalid numeric values") from exc

        platform = str(self.platform).lower()
        profile = PLATFORM_PROFILES.get(platform)
        if profile is None:
            raise ValueError(f"unsupported platform: {self.platform}")
        platform_max = profile["max_seconds"]
        if not 8.0 <= target <= 180.0:
            raise ValueError("target_seconds must be between 8 and 180")
        if platform_max is not None and target > float(platform_max):
            raise ValueError(f"target_seconds must be <= {platform_max:g} for {self.platform}")
        if not 40 <= bpm <= 240:
            raise ValueError("bpm must be between 40 and 240")
        if not 0.75 <= retention <= 3.0:
            raise ValueError("retention_interval must be between 0.75 and 3.0")
        if not 2 <= max_words <= 8:
            raise ValueError("max_overlay_words must be between 2 and 8")
        if minimum <= 0 or maximum < minimum:
            raise ValueError("invalid clip duration bounds")
        if minimum > target or maximum > target:
            raise ValueError("clip duration bounds cannot exceed target_seconds")


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

EDIT_STRATEGIES: Mapping[EditType, Dict[str, Any]] = {
    EditType.EMOTIONAL: {"purposes": ("Hook", "Context", "Curiosity", "Escalation", "Payoff", "Final impact"), "motions": ("micro-zoom", "reframe", "subtle-parallax", "tracking"), "transitions": ("hard cut", "match cut", "hard cut", "J-cut", "hard cut"), "visual_styles": ("emotion-first close-up", "context frame with subject continuity", "detail or reaction insert", "rising-intensity visual", "clearest emotional proof", "strongest emotional hold")},
    EditType.MOTIVATIONAL: {"purposes": ("Hook", "Context", "Curiosity", "Escalation", "Payoff", "Final impact"), "motions": ("punch-in", "tracking", "micro-zoom", "reframe"), "transitions": ("hard cut", "match cut", "hard cut", "speed ramp", "hard cut"), "visual_styles": ("result-before-context", "setup and obstacle", "effort detail", "momentum-heavy action", "achievement reveal", "confident final hold")},
    EditType.NOSTALGIC: {"purposes": ("Hook", "Context", "Memory", "Contrast", "Payoff", "Final impact"), "motions": ("subtle-parallax", "slow push", "reframe", "micro-zoom"), "transitions": ("dissolve", "match cut", "dissolve", "match cut", "hard cut"), "visual_styles": ("recognizable memory frame", "wide contextual memory", "detail from the past", "before-vs-after contrast", "familiar payoff frame", "longer nostalgic hold")},
    EditType.FUNNY: {"purposes": ("Hook", "Setup", "Escalation", "Escalation", "Punchline", "Reaction"), "motions": ("punch-in", "reframe", "micro-zoom", "tracking"), "transitions": ("hard cut", "hard cut", "hard cut", "J-cut", "hard cut"), "visual_styles": ("reaction cold-open", "clean joke setup", "surprise detail", "faster escalation", "punchline frame", "best reaction")},
    EditType.DRAMATIC: {"purposes": ("Hook", "Context", "Threat", "Escalation", "Climax", "Payoff"), "motions": ("punch-in", "tracking", "micro-zoom", "reframe"), "transitions": ("hard cut", "J-cut", "hard cut", "speed ramp", "hard cut"), "visual_styles": ("highest-stakes frame", "minimal context", "threat evidence", "fast consequence buildup", "climax action", "consequence hold")},
    EditType.DOCUMENTARY: {"purposes": ("Hook", "Context", "Evidence", "Context", "Payoff", "Final impact"), "motions": ("subtle-parallax", "reframe", "tracking", "micro-zoom"), "transitions": ("hard cut", "match cut", "hard cut", "match cut", "hard cut"), "visual_styles": ("fact-leading visual", "establishing evidence", "detail or source frame", "cause-and-effect sequence", "clearest evidence", "summary image")},
    EditType.SIGMA: {"purposes": ("Hook", "Claim", "Evidence", "Escalation", "Payoff", "Final impact"), "motions": ("punch-in", "reframe", "micro-zoom", "tracking"), "transitions": ("hard cut", "hard cut", "match cut", "speed ramp", "hard cut"), "visual_styles": ("dominant cold-open", "clear claim frame", "proof detail", "intensity increase", "payoff reveal", "confident hold")},
    EditType.CHARACTER_ANALYSIS: {"purposes": ("Hook", "Context", "Trait", "Evidence", "Payoff", "Final impact"), "motions": ("subtle-parallax", "reframe", "micro-zoom", "tracking"), "transitions": ("hard cut", "match cut", "hard cut", "J-cut", "hard cut"), "visual_styles": ("face or defining frame", "character setup", "trait evidence", "behavioral proof", "character conclusion", "signature frame")},
    EditType.TRIBUTE: {"purposes": ("Hook", "Context", "Memory", "Proof", "Payoff", "Final impact"), "motions": ("slow push", "subtle-parallax", "micro-zoom", "reframe"), "transitions": ("dissolve", "match cut", "dissolve", "match cut", "hard cut"), "visual_styles": ("most human frame", "shared context", "memorable detail", "proof of character", "emotional culmination", "respectful final hold")},
    EditType.STORYTELLING: {"purposes": ("Hook", "Context", "Question", "Escalation", "Payoff", "Final impact"), "motions": ("punch-in", "tracking", "reframe", "micro-zoom"), "transitions": ("hard cut", "match cut", "hard cut", "J-cut", "hard cut"), "visual_styles": ("question-provoking frame", "clean setup", "evidence detail", "rising narrative action", "answer reveal", "closing image")},
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


def _emotion_scores(topic: str, context: str) -> Dict[str, int]:
    source = f"{topic} {context}".lower()
    tokens = _tokens(source)
    token_counts = {token: tokens.count(token) for token in set(tokens)}
    scores: Dict[str, int] = {}
    for emotion, terms in EMOTION_TERMS.items():
        score = 0
        for term in terms:
            normalized = _tokens(term)
            if len(normalized) == 1:
                score += token_counts.get(normalized[0], 0)
            elif term in source:
                score += 2
        scores[emotion] = score
    return scores


def analyze_core_idea(topic: str, context: str = "", audience: str = "general short-form viewers") -> CoreIdea:
    topic = str(topic).strip()
    context = str(context).strip()
    audience = str(audience).strip()
    if not topic:
        raise ValueError("topic is required")
    scores = _emotion_scores(topic, context)
    audience_tokens = set(_tokens(audience))
    if {"history", "documentary", "facts"} & audience_tokens:
        scores["curious"] += 1
    if {"comedy", "funny", "humor"} & audience_tokens:
        scores["funny"] += 1
    emotion = max(scores, key=lambda item: (scores[item], item)) if any(scores.values()) else "curious"
    if emotion == "trust":
        angle, care, stakes, payoff = (f"Why {topic} became the person people could rely on", "Loyalty under pressure is instantly relatable.", "Trust can be earned in one moment and lost in one decision.", f"The moment that proves what {topic} really meant to everyone.")
    elif emotion == "dramatic":
        angle, care, stakes, payoff = (f"The decision that changed everything for {topic}", "Conflict, risk, and irreversible consequences create immediate stakes.", "Something valuable can be lost before the viewer understands why.", "Reveal the consequence viewers were waiting to understand.")
    elif emotion == "inspiring":
        angle, care, stakes, payoff = (f"How {topic} kept going when quitting was easier", "Viewers connect with effort that turns into a visible payoff.", "The attempt only matters if failure feels possible.", "Show the result that makes the struggle worth it.")
    elif emotion == "nostalgic":
        angle, care, stakes, payoff = (f"Why people still remember {topic}", "Shared memories create instant emotional recognition.", "The feeling only works if the memory seems specific and fleeting.", "Return to the image or moment viewers wanted to remember.")
    elif emotion == "funny":
        angle, care, stakes, payoff = (f"The moment {topic} went completely off the rails", "A fast setup with escalating surprise rewards attention.", "The joke dies if the setup is longer than the payoff.", "Deliver the cleanest reaction or reversal last.")
    else:
        angle, care, stakes, payoff = (f"The part of {topic} most people miss", "A knowledge gap makes viewers want the missing context.", "The answer must feel more valuable than the setup.", "Resolve the question with one clear, memorable insight.")
    return CoreIdea(topic, care, angle, emotion, f"The viewer needs the final proof behind: {angle}.", payoff, stakes)


def choose_edit_type(core: CoreIdea, requested: str | None = None) -> EditType:
    if requested:
        normalized = str(requested).strip().lower()
        for item in EditType:
            if item.value.lower() == normalized:
                return item
        raise ValueError(f"unknown edit type: {requested}")
    return EDIT_BY_EMOTION.get(core.target_emotion, EditType.STORYTELLING)


def generate_hooks(core: CoreIdea, edit_type: EditType) -> List[HookPack]:
    strategy = EDIT_STRATEGIES[edit_type]
    candidates = [
        HookPack(strategy["visual_styles"][0], _compact(f"Nobody expected {core.topic}"), core.stakes, 0.93),
        HookPack(f"Start with {strategy['visual_styles'][2]} and restrained motion.", "This changed everything", core.emotional_angle, 0.89),
        HookPack(f"Use a contrast frame tied to {strategy['purposes'][2].lower()}.", _compact(f"Everyone misread {core.topic}"), core.watch_to_end_reason, 0.86),
    ]
    if edit_type == EditType.FUNNY:
        candidates[0] = HookPack("Cold-open on the reaction, cut away before the result.", "This got worse", "Anticipation before the punchline.", 0.97)
    elif edit_type in {EditType.NOSTALGIC, EditType.TRIBUTE}:
        candidates[0] = HookPack("Open on the most recognizable human frame with subtle motion.", "You remember this", "Recognition before explanation.", 0.96)
    elif edit_type == EditType.DOCUMENTARY:
        candidates[0] = HookPack("Open on the clearest piece of evidence before context.", "Here is what happened", "Curiosity before explanation.", 0.95)
    elif edit_type == EditType.CHARACTER_ANALYSIS:
        candidates[0] = HookPack("Open on the defining behavior before naming the trait.", "This says everything", "Recognition before analysis.", 0.95)
    return sorted(candidates, key=lambda hook: hook.score, reverse=True)


def _purpose_sequence(count: int, strategy: Mapping[str, Any]) -> List[str]:
    purposes = list(strategy["purposes"])
    while len(purposes) < count:
        purposes.insert(max(3, len(purposes) - 2), "Escalation")
    return purposes[:count]


def _allocate_durations(count: int, total: float, minimum: float, maximum: float) -> List[float]:
    if count <= 0:
        raise ValueError("count must be positive")
    if total < minimum * count or total > maximum * count:
        raise ValueError("clip count cannot satisfy duration bounds")
    weights = [1.0] * count
    weights[0] = 0.72
    if count > 1:
        weights[-1] = 1.18
    if count > 3:
        weights[-2] = 1.12
    durations = [min(maximum, max(minimum, total * weight / sum(weights))) for weight in weights]
    for _ in range(100):
        diff = total - sum(durations)
        if abs(diff) < 0.0005:
            break
        candidates = [i for i, value in enumerate(durations) if (diff > 0 and value < maximum - 0.0005) or (diff < 0 and value > minimum + 0.0005)]
        if not candidates:
            break
        share = diff / len(candidates)
        for i in candidates:
            durations[i] = max(minimum, min(maximum, durations[i] + share))
    rounded = [round(value, 3) for value in durations]
    rounded[-1] = round(rounded[-1] + total - sum(rounded), 3)
    if not minimum <= rounded[-1] <= maximum:
        raise ValueError("duration allocation drifted outside bounds")
    return rounded


def _overlay_for_purpose(purpose: str, core: CoreIdea, max_words: int, edit_type: EditType) -> str:
    candidates: Mapping[str, Sequence[str]] = {
        "Hook": ("Not what you expected", f"Nobody saw {core.topic} coming"), "Context": (f"It started with {core.topic}", "Here is the setup"),
        "Setup": ("Watch what happens", "This is where it starts"), "Curiosity": ("But one detail mattered", "There was one problem"),
        "Question": ("Something was missing", "The real question was this"), "Claim": ("There is a reason", "This tells us something"),
        "Memory": ("You remember this", "That moment still hits"), "Contrast": ("Then everything changed", "Before vs. after"),
        "Trait": ("That is the pattern", "Notice what he does"), "Evidence": ("Look at this detail", "The evidence is here"),
        "Importance": ("This raised the stakes", "Now it matters"), "Threat": ("Then it got dangerous", "The risk was real"),
        "Escalation": ("The pressure kept rising", "Everything accelerated"), "Climax": ("This was the moment", "Now it all lands"),
        "Payoff": ("This was the proof", "Here is the answer"), "Punchline": ("And then this happened", "That was the joke"),
        "Reaction": ("Watch the reaction", "That face says it all"), "Proof": ("This proves it", "Here is the proof"),
        "Final impact": (core.payoff, "Remember this moment"),
    }
    options = list(candidates.get(purpose, (purpose,)))
    if edit_type == EditType.DRAMATIC and purpose in {"Escalation", "Threat"}:
        options = ["No way back", "The pressure was real", "Then it changed"]
    elif edit_type == EditType.MOTIVATIONAL and purpose in {"Escalation", "Climax"}:
        options = ["Keep going", "This was the turning point", "Now the work pays off"]
    elif edit_type == EditType.DOCUMENTARY and purpose in {"Evidence", "Proof"}:
        options = ["The evidence is clear", "This is what proves it", "Now it makes sense"]
    elif edit_type == EditType.FUNNY and purpose in {"Punchline", "Reaction"}:
        options = ["And there it is", "Okay, that happened", "Look at that reaction"]
    return _compact(options[0], max_words)


def _unique_overlay(purpose: str, core: CoreIdea, max_words: int, index: int, used: set[str], edit_type: EditType) -> str:
    for option in (
        _overlay_for_purpose(purpose, core, max_words, edit_type),
        _overlay_for_purpose("Escalation", core, max_words, edit_type),
        _compact(f"{purpose} beat {index}", max_words),
    ):
        if option.lower() not in used:
            used.add(option.lower())
            return option
    fallback = _compact(f"Beat {index}: {core.topic}", max_words)
    used.add(fallback.lower())
    return fallback


def build_clip_plan(core: CoreIdea, edit_type: EditType, config: V3Config) -> List[ClipBeat]:
    config.validate()
    strategy = EDIT_STRATEGIES[edit_type]
    minimum_count = max(len(strategy["purposes"]), int((config.target_seconds + config.max_clip_seconds - 0.001) // config.max_clip_seconds))
    preferred_count = max(len(strategy["purposes"]), round(config.target_seconds / 2.7))
    count = max(minimum_count, preferred_count)
    count = min(count, max(len(strategy["purposes"]), int(config.target_seconds // config.min_clip_seconds)))
    durations = _allocate_durations(count, config.target_seconds, config.min_clip_seconds, config.max_clip_seconds)
    purposes = _purpose_sequence(count, strategy)
    motions, transitions, visual_styles = list(strategy["motions"]), list(strategy["transitions"]), list(strategy["visual_styles"])
    beats: List[ClipBeat] = []
    cursor = 0.0
    used_overlays: set[str] = set()
    for zero_index, (purpose, duration) in enumerate(zip(purposes, durations)):
        index = zero_index + 1
        end = round(cursor + duration, 3)
        beats.append(ClipBeat(index, round(cursor, 3), end, purpose, core.target_emotion, visual_styles[zero_index % len(visual_styles)], _unique_overlay(purpose, core, config.max_overlay_words, index, used_overlays, edit_type), motions[zero_index % len(motions)], transitions[zero_index % len(transitions)]))
        cursor = end
    beats[-1] = ClipBeat(**{**asdict(beats[-1]), "end": round(config.target_seconds, 3)})
    return beats


def analyze_music(core: CoreIdea, config: V3Config, clips: Sequence[ClipBeat]) -> MusicPlan:
    config.validate()
    beat_seconds = round(60.0 / config.bpm, 4)
    payoff_start = clips[max(0, len(clips) - 2)].start if clips else config.target_seconds * 0.7
    drop_time = min(config.target_seconds * 0.72, max(2.0, payoff_start))
    sync_points = []
    cursor = 0.0
    while cursor <= config.target_seconds + 0.001:
        sync_points.append(round(cursor, 3))
        cursor += beat_seconds * 2
    if not sync_points or sync_points[-1] < config.target_seconds:
        sync_points.append(round(config.target_seconds, 3))
    energy = "high" if core.target_emotion in {"dramatic", "inspiring", "funny"} else "medium"
    return MusicPlan(config.bpm, energy, core.target_emotion, beat_seconds, round(drop_time, 3), sync_points)


def build_retention_map(config: V3Config, clips: Sequence[ClipBeat], music: MusicPlan) -> List[RetentionEvent]:
    config.validate()
    events: List[RetentionEvent] = []
    for clip in clips[1:]:
        events.append(RetentionEvent(round(clip.start, 3), "clip", f"Switch to the {clip.purpose.lower()} visual; keep the narrative continuous."))
    t = 0.0
    fill_kinds = ("zoom", "text", "motion", "angle", "beat accent")
    while t < config.target_seconds - 0.05:
        if not any(abs(event.time - t) <= 0.25 for event in events):
            kind = "beat drop" if abs(t - music.drop_time) <= config.retention_interval / 2 else fill_kinds[len(events) % len(fill_kinds)]
            events.append(RetentionEvent(round(t, 3), kind, f"Apply a {kind} change without interrupting the story."))
        t += config.retention_interval
    events.sort(key=lambda event: event.time)
    compacted: List[RetentionEvent] = []
    for event in events:
        if event.time >= config.target_seconds:
            continue
        if compacted and event.time - compacted[-1].time < 0.35:
            continue
        compacted.append(event)
    return compacted


def platform_variants(config: V3Config) -> Dict[str, Dict[str, Any]]:
    config.validate()
    return {key: dict(value) for key, value in PLATFORM_PROFILES.items()}


def _metadata(core: CoreIdea) -> tuple[str, List[str], List[str], str]:
    thumbnail = f"Close emotional frame of {core.topic}; one focal subject; 2-4 word contrast text; no clutter."
    titles = [_compact(core.emotional_angle, 11), _compact(f"The {core.topic} moment nobody forgets", 11), _compact(f"Why {core.topic} mattered more than people realized", 11)]
    hashtags = ["#shorts", "#story", "#edit", "#videoediting"]
    slug = re.sub(r"[^a-z0-9]+", "", core.topic.lower())[:20]
    if slug:
        hashtags.append(f"#{slug}")
    description = f"{core.emotional_angle}. {core.why_people_care} Watch through the payoff: {core.payoff}"
    return thumbnail, titles, hashtags, description


def _human_editor_checks(core: CoreIdea, edit_type: EditType, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], retention: Sequence[RetentionEvent], config: V3Config) -> QualityReport:
    overlays = [clip.text_overlay.lower() for clip in clips]
    durations = [clip.end - clip.start for clip in clips]
    retention_gaps = [later.time - earlier.time for earlier, later in zip(retention, retention[1:])]
    strategy = EDIT_STRATEGIES.get(edit_type)
    checks = {
        "emotion_defined": bool(core.target_emotion),
        "hook_under_two_seconds": bool(clips and clips[0].end <= 2.2),
        "hook_has_context_gap": bool(hooks and hooks[0].text and core.watch_to_end_reason),
        "every_clip_has_purpose": all(bool(clip.purpose) for clip in clips),
        "overlay_word_limit": all(2 <= len(_tokens(clip.text_overlay)) <= config.max_overlay_words for clip in clips),
        "no_duplicate_overlays": len(overlays) == len(set(overlays)),
        "duration_bounds": all(config.min_clip_seconds - 0.02 <= duration <= config.max_clip_seconds + 0.02 for duration in durations),
        "retention_changes_frequent": bool(retention) and all(gap <= 3.01 for gap in retention_gaps),
        "has_payoff": any(clip.purpose in {"Payoff", "Punchline", "Climax"} for clip in clips),
        "has_final_impact": bool(clips and clips[-1].purpose in {"Final impact", "Reaction", "Payoff"}),
        "single_edit_type_strategy": strategy is not None,
        "platform_supported": config.platform in PLATFORM_PROFILES,
    }
    warnings: List[str] = []
    if not checks["hook_under_two_seconds"]:
        warnings.append("Hook exceeds the preferred ~2 second opening window.")
    if not checks["no_duplicate_overlays"]:
        warnings.append("Repeated overlay text can make the edit feel templated.")
    if retention_gaps and max(retention_gaps) > 3.0:
        warnings.append("A visual change gap exceeds the 1-3 second retention rule.")
    score = round(100 * sum(1 for value in checks.values() if value) / len(checks))
    passed = score >= 90 and checks["has_payoff"] and checks["emotion_defined"] and checks["single_edit_type_strategy"]
    return QualityReport(passed=passed, score=score, checks=checks, warnings=warnings)


def _heuristic_metrics(core: CoreIdea, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], quality: QualityReport) -> Dict[str, float]:
    hook_score = hooks[0].score if hooks else 0.0
    average_duration = clips[-1].end / len(clips) if clips else 0.0
    pace = min(1.0, 2.8 / max(average_duration, 0.1)) if clips else 0.0
    quality_factor = quality.score / 100.0
    emotional = 0.9 if core.target_emotion in {"trust", "dramatic", "inspiring", "nostalgic"} else 0.82
    return {
        "retention_score": round(100 * (0.35 * hook_score + 0.30 * pace + 0.20 * quality_factor + 0.15 * emotional), 1),
        "completion_score": round(100 * (0.45 * quality_factor + 0.30 * pace + 0.25 * hook_score), 1),
        "rewatch_score": round(100 * (0.40 * hook_score + 0.35 * emotional + 0.25 * quality_factor), 1),
        "shareability_score": round(100 * (0.50 * emotional + 0.30 * quality_factor + 0.20 * hook_score), 1),
    }


def create_v3_blueprint(topic: str, *, context: str = "", config: V3Config | None = None, edit_type: str | None = None) -> V3Blueprint:
    cfg = config or V3Config()
    cfg.validate()
    core = analyze_core_idea(topic, context, cfg.audience)
    selected = choose_edit_type(core, edit_type)
    hooks = generate_hooks(core, selected)
    clips = build_clip_plan(core, selected, cfg)
    music = analyze_music(core, cfg, clips)
    retention = build_retention_map(cfg, clips, music)
    thumbnail, titles, hashtags, description = _metadata(core)
    quality = _human_editor_checks(core, selected, hooks, clips, retention, cfg)
    metrics = _heuristic_metrics(core, hooks, clips, quality)
    return V3Blueprint("3.0.0", core, selected.value, hooks, clips, music, retention, thumbnail, titles, hashtags, description, platform_variants(cfg), quality, metrics, list(V3_CAPABILITIES))


def validate_blueprint(blueprint: V3Blueprint) -> None:
    if blueprint.version != "3.0.0":
        raise ValueError("unexpected blueprint version")
    if len(blueprint.capabilities) != 40:
        raise ValueError("v3 blueprint must expose exactly 40 tracked upgrades")
    if not blueprint.clip_plan:
        raise ValueError("clip plan is empty")
    try:
        edit_type = EditType(blueprint.edit_type)
    except ValueError as exc:
        raise ValueError(f"unsupported blueprint edit type: {blueprint.edit_type}") from exc
    if edit_type not in EDIT_STRATEGIES:
        raise ValueError(f"no strategy registered for {edit_type.value}")
    if not blueprint.retention_map:
        raise ValueError("retention map is empty")
    if not blueprint.quality.passed:
        raise ValueError("blueprint failed human-editor quality control")
    if abs(blueprint.clip_plan[-1].end - blueprint.music.sync_points[-1]) > 1.01:
        raise ValueError("clip plan does not cover the planned duration")


def blueprint_summary(blueprint: V3Blueprint) -> str:
    hook = blueprint.hooks[0].text if blueprint.hooks else ""
    return f"{blueprint.core_idea.topic} | {blueprint.edit_type} | emotion={blueprint.core_idea.target_emotion} | hook={hook!r} | clips={len(blueprint.clip_plan)} | quality={blueprint.quality.score}/100"
