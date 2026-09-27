"""Edit Factory v3 creative planning, semantic analysis, retention and QC."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence
from types import MappingProxyType

from .v3_semantics import combined_scores
from .v3_scoring import heuristic_metrics


class Platform(str, Enum):
    YOUTUBE_SHORTS = "youtube_shorts"
    TIKTOK = "tiktok"
    INSTAGRAM_REELS = "instagram_reels"
    SQUARE = "square"
    YOUTUBE = "youtube"


class RetentionKind(str, Enum):
    BEAT_DROP = "beat drop"
    CLIP = "clip"
    ZOOM = "zoom"
    TEXT = "text"
    MOTION = "motion"
    ANGLE = "angle"


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
        platform_value = self.platform.value if isinstance(self.platform, Platform) else str(self.platform)
        profile = PLATFORM_PROFILES.get(platform_value.lower())
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
    sync_points: Sequence[float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sync_points", tuple(float(value) for value in self.sync_points))


@dataclass(frozen=True)
class RetentionEvent:
    time: float
    kind: str
    instruction: str


@dataclass(frozen=True)
class PlatformProfile:
    width: int
    height: int
    max_seconds: float | None
    safe_bottom: int
    cta: str


@dataclass(frozen=True)
class QCRequirements:
    target_tolerance_seconds: float = 0.08
    require_video: bool = True
    require_audio: bool = False
    require_independent_retention: bool = True
    semantic_qc_enabled: bool = True


@dataclass(frozen=True)
class PackagingPlan:
    final_video_name: str = "final.v3.mp4"
    thumbnail_name: str = "thumbnail.png"
    vertical_thumbnail_name: str = "thumbnail_vertical.png"
    upload_manifest_name: str = "upload_package.json"


@dataclass(frozen=True)
class MetricMetadata:
    method: str = "heuristic"
    confidence: str = "low"
    calibration_status: str = "uncalibrated"


@dataclass(frozen=True)
class QualityReport:
    passed: bool
    score: int
    checks: Mapping[str, bool] = field(default_factory=dict)
    warnings: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "checks", MappingProxyType({str(k): bool(v) for k, v in dict(self.checks).items()}))
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings))


@dataclass(frozen=True)
class V3Blueprint:
    version: str
    core_idea: CoreIdea
    edit_type: str
    hooks: Sequence[HookPack]
    clip_plan: Sequence[ClipBeat]
    music: MusicPlan
    retention_map: Sequence[RetentionEvent]
    thumbnail_concept: str
    title_options: Sequence[str]
    hashtags: Sequence[str]
    description: str
    platform_variants: Mapping[str, Mapping[str, Any]]
    quality: QualityReport
    metrics: Mapping[str, float]
    capabilities: Sequence[str]
    platform: str = "youtube_shorts"
    audience: str = "general short-form viewers"
    platform_profile: PlatformProfile | None = None
    qc: QCRequirements = field(default_factory=QCRequirements)
    packaging: PackagingPlan = field(default_factory=PackagingPlan)
    metric_metadata: MetricMetadata = field(default_factory=MetricMetadata)

    def __post_init__(self) -> None:
        object.__setattr__(self, "hooks", tuple(self.hooks))
        object.__setattr__(self, "clip_plan", tuple(self.clip_plan))
        object.__setattr__(self, "retention_map", tuple(self.retention_map))
        object.__setattr__(self, "title_options", tuple(self.title_options))
        object.__setattr__(self, "hashtags", tuple(self.hashtags))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "platform_variants", _freeze_mapping(self.platform_variants))
        object.__setattr__(self, "metrics", MappingProxyType({str(k): float(v) for k, v in dict(self.metrics).items()}))
        platform_value = self.platform.value if isinstance(self.platform, Platform) else str(self.platform).strip().lower()
        object.__setattr__(self, "platform", platform_value)
        if self.platform_profile is None:
            profile = PLATFORM_PROFILES.get(platform_value)
            if profile is None:
                raise ValueError(f"unsupported blueprint platform: {self.platform}")
            object.__setattr__(self, "platform_profile", PlatformProfile(**profile))

    @property
    def schema_version(self) -> str:
        return self.version

    @property
    def duration(self) -> float:
        return float(self.clip_plan[-1].end) if self.clip_plan else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "schema_version": self.schema_version,
            "platform": self.platform,
            "audience": self.audience,
            "platform_profile": asdict(self.platform_profile),
            "qc": asdict(self.qc),
            "packaging": asdict(self.packaging),
            "metric_metadata": asdict(self.metric_metadata),
            "core_idea": asdict(self.core_idea),
            "edit_type": self.edit_type,
            "hooks": [asdict(item) for item in self.hooks],
            "clip_plan": [asdict(item) for item in self.clip_plan],
            "music": asdict(self.music),
            "retention_map": [asdict(item) for item in self.retention_map],
            "thumbnail_concept": self.thumbnail_concept,
            "title_options": list(self.title_options),
            "hashtags": list(self.hashtags),
            "description": self.description,
            "platform_variants": {str(k): dict(v) for k, v in self.platform_variants.items()},
            "quality": {"passed": self.quality.passed, "score": self.quality.score, "checks": dict(self.quality.checks), "warnings": list(self.quality.warnings)},
            "metrics": dict(self.metrics),
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "V3Blueprint":
        if not isinstance(payload, Mapping):
            raise ValueError("V3 blueprint payload must be an object")
        required = {
            "version", "core_idea", "edit_type", "hooks", "clip_plan", "music",
            "retention_map", "thumbnail_concept", "title_options", "hashtags",
            "description", "platform_variants", "quality", "metrics", "capabilities",
        }
        allowed = required | {"schema_version", "platform", "audience", "platform_profile", "qc", "packaging", "metric_metadata", "source_metadata"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f"V3 blueprint contains unknown fields: {', '.join(unknown)}")
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"V3 blueprint is missing required fields: {', '.join(missing)}")
        if payload.get("version") != "3.0.0" or payload.get("schema_version", "3.0.0") != "3.0.0":
            raise ValueError("unsupported V3 blueprint schema version")
        if not isinstance(payload["hooks"], (list, tuple)) or not isinstance(payload["clip_plan"], (list, tuple)):
            raise ValueError("V3 blueprint hooks and clip_plan must be arrays")
        if not isinstance(payload["retention_map"], (list, tuple)) or not isinstance(payload["capabilities"], (list, tuple)):
            raise ValueError("V3 blueprint retention_map and capabilities must be arrays")
        if not isinstance(payload["title_options"], (list, tuple)) or not isinstance(payload["hashtags"], (list, tuple)):
            raise ValueError("V3 blueprint title_options and hashtags must be arrays")
        if not isinstance(payload["metrics"], Mapping) or not isinstance(payload["platform_variants"], Mapping):
            raise ValueError("V3 blueprint metrics and platform_variants must be objects")
        try:
            core = CoreIdea(**dict(payload["core_idea"]))
            hooks = tuple(HookPack(**dict(item)) for item in payload["hooks"])
            clips = tuple(ClipBeat(**dict(item)) for item in payload["clip_plan"])
            music_data = dict(payload["music"])
            music_data["sync_points"] = tuple(float(x) for x in music_data["sync_points"])
            music = MusicPlan(**music_data)
            retention = tuple(RetentionEvent(**dict(item)) for item in payload["retention_map"])
            quality = QualityReport(**dict(payload["quality"]))
            platform_name = str(payload.get("platform", "youtube_shorts"))
            raw_profile = payload.get("platform_profile")
            profile = PlatformProfile(**dict(raw_profile)) if isinstance(raw_profile, Mapping) else PlatformProfile(**dict(PLATFORM_PROFILES.get(platform_name, {})))
            raw_qc = payload.get("qc")
            qc = QCRequirements(**dict(raw_qc)) if isinstance(raw_qc, Mapping) else QCRequirements()
            raw_packaging = payload.get("packaging")
            packaging = PackagingPlan(**dict(raw_packaging)) if isinstance(raw_packaging, Mapping) else PackagingPlan()
            raw_metric_metadata = payload.get("metric_metadata")
            metric_metadata = MetricMetadata(**dict(raw_metric_metadata)) if isinstance(raw_metric_metadata, Mapping) else MetricMetadata()
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("V3 blueprint contains malformed typed data") from exc
        result = cls(
            version=str(payload["version"]),
            core_idea=core,
            edit_type=str(payload["edit_type"]),
            hooks=hooks,
            clip_plan=clips,
            music=music,
            retention_map=retention,
            thumbnail_concept=str(payload["thumbnail_concept"]),
            title_options=tuple(payload["title_options"]),
            hashtags=tuple(payload["hashtags"]),
            description=str(payload["description"]),
            platform_variants=dict(payload["platform_variants"]),
            quality=quality,
            metrics=dict(payload["metrics"]),
            capabilities=tuple(payload["capabilities"]),
            platform=str(payload.get("platform", "youtube_shorts")),
            audience=str(payload.get("audience", "general short-form viewers")),
            platform_profile=profile,
            qc=qc,
            packaging=packaging,
            metric_metadata=metric_metadata,
        )
        validate_blueprint(result)
        return result


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("platform_variants must be an object")
    frozen = {}
    for key, item in value.items():
        if not isinstance(item, Mapping):
            raise ValueError("platform variant must be an object")
        frozen[key] = MappingProxyType(dict(item))
    return MappingProxyType(frozen)


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


def _normalize_sentence(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip()).strip(" .,-")
    return value + "." if value else ""

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
        "trust": (
            f"Why {topic} became impossible to ignore",
            "Loyalty under pressure creates immediate human stakes.",
            "Trust can be earned quickly and lost in one decision.",
            f"The final proof that changes how viewers read {topic}.",
        ),
        "dramatic": (
            f"The decision that changed everything in {topic}",
            "Conflict and consequences create an immediate information gap.",
            "Something valuable can be lost before the viewer understands why.",
            "Reveal the consequence viewers were waiting to understand.",
        ),
        "inspiring": (
            f"How {topic} kept going when quitting was easier",
            "Effort matters when failure feels possible and progress is visible.",
            "The attempt only matters if the outcome is uncertain.",
            "Show the result that makes the struggle worth it.",
        ),
        "nostalgic": (
            f"Why {topic} still feels unforgettable",
            "Recognition and shared memory create instant emotional pull.",
            "The memory only works when the details feel specific.",
            "Return to the moment viewers wanted to remember.",
        ),
        "funny": (
            f"The moment {topic} went completely off the rails",
            "Fast setup plus escalation makes the reversal worth waiting for.",
            "The joke dies when setup overwhelms the punchline.",
            "Deliver the cleanest reaction or reversal last.",
        ),
        "curious": (
            f"The part of {topic} most people miss",
            "A knowledge gap gives the viewer a reason to stay.",
            "The answer must be more valuable than the setup.",
            "Resolve the question with one clear memorable insight.",
        ),
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
    topic = _compact(core.topic, 7)
    hooks = [
        HookPack(
            strategy["visual_styles"][0],
            _compact(f"The moment {topic} changed"),
            core.stakes,
            0.94,
        ),
        HookPack(
            f"Open on {strategy['visual_styles'][2]} before context.",
            _compact(f"This changed {topic}"),
            core.emotional_angle,
            0.90,
        ),
        HookPack(
            f"Use a contrast built around {strategy['purposes'][2].lower()}.",
            _compact(f"What they missed about {topic}"),
            core.watch_to_end_reason,
            0.87,
        ),
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
    return heuristic_metrics(hook=hook, pace=pace, quality=q, emotion=emotion)


def _metadata(core: CoreIdea) -> tuple[str, List[str], List[str], str]:
    """Build compact, topic-specific metadata for every V3 downstream consumer."""
    topic = _compact(core.topic, 8)
    angle = _compact(core.emotional_angle, 10)
    payoff = _compact(core.payoff, 12)
    emotion = _compact(core.target_emotion, 3)

    titles = [
        _compact(f"{topic}: {angle}", 14),
        _compact(f"The moment {topic} changed", 10),
        _compact(f"What happened with {topic}", 10),
        _compact(f"The part of {topic} most people miss", 12),
        _compact(f"Why {topic} still matters", 10),
        _compact(f"The turning point in {topic}", 11),
        _compact(f"{topic}: the detail that changes the story", 12),
        _compact(f"How {topic} changed everything", 10),
    ]
    titles = list(dict.fromkeys(title for title in titles if title))

    topic_tags = [
        token
        for token in _tokens(core.topic)
        if len(token) >= 3
    ]
    tags = list(dict.fromkeys([*topic_tags, emotion]))
    description_parts = [
        _normalize_sentence(core.emotional_angle),
        _normalize_sentence(core.why_people_care),
        _normalize_sentence(payoff),
    ]
    description = " ".join(part for part in description_parts if part).strip()
    thumbnail = (
        f"Use the strongest focal frame from {topic}, keep the subject clear, "
        f"leave text-safe space, and use a bold 2-5 word hook tied to the story."
    )
    return thumbnail, titles[:10], tags[:12], description

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
    platform_value = cfg.platform.value if isinstance(cfg.platform, Platform) else str(cfg.platform).strip().lower()
    blueprint = V3Blueprint("3.0.0", core, selected.value, hooks, clips, music, retention, thumbnail, titles, tags, description, platform_variants(cfg), quality, metrics, list(V3_CAPABILITIES), platform=platform_value, audience=cfg.audience)
    validate_blueprint(blueprint)
    return blueprint

def validate_blueprint(blueprint: V3Blueprint) -> None:
    if not isinstance(blueprint, V3Blueprint):
        raise ValueError("expected a V3Blueprint instance")
    if blueprint.version != "3.0.0":
        raise ValueError("unexpected blueprint version")
    if len(blueprint.capabilities) != 40 or len(set(blueprint.capabilities)) != 40:
        raise ValueError("v3 blueprint must expose exactly 40 unique tracked capabilities")
    if blueprint.platform not in PLATFORM_PROFILES:
        raise ValueError(f"unsupported blueprint platform: {blueprint.platform}")
    expected_profile = PLATFORM_PROFILES[blueprint.platform]
    if asdict(blueprint.platform_profile) != dict(expected_profile):
        raise ValueError("blueprint platform profile does not match registered platform constraints")
    if not 0.01 <= float(blueprint.qc.target_tolerance_seconds) <= 1.0:
        raise ValueError("blueprint QC target tolerance is invalid")
    if blueprint.metric_metadata.method != "heuristic" or blueprint.metric_metadata.confidence not in {"low", "medium", "high"}:
        raise ValueError("blueprint metric metadata is invalid")

    if not str(blueprint.audience).strip():
        raise ValueError("blueprint audience is required")
    if not blueprint.platform_variants or blueprint.platform not in blueprint.platform_variants:
        raise ValueError("blueprint platform variants are incomplete")
    try:
        edit_type = EditType(blueprint.edit_type)
    except ValueError as exc:
        raise ValueError(f"unsupported blueprint edit type: {blueprint.edit_type}") from exc
    if edit_type not in EDIT_STRATEGIES or not blueprint.clip_plan:
        raise ValueError("blueprint is missing a registered creative strategy")
    if not blueprint.retention_map:
        raise ValueError("blueprint retention map is empty")
    previous_retention = -1e-6
    for event in blueprint.retention_map:
        try:
            RetentionKind(event.kind)
        except ValueError as exc:
            raise ValueError(f"unsupported retention event kind: {event.kind}") from exc
        if not math.isfinite(float(event.time)) or event.time < -0.001 or event.time > blueprint.duration + 0.001:
            raise ValueError("retention event time is outside the blueprint timeline")
        if event.time < previous_retention:
            raise ValueError("retention map is not monotonic")
        if not str(event.instruction).strip():
            raise ValueError("retention event instruction is required")
        previous_retention = event.time
    previous = -1e-6
    for expected_index, clip in enumerate(blueprint.clip_plan, start=1):
        if clip.index != expected_index:
            raise ValueError("clip plan indices must be contiguous and 1-based")
        if not all(math.isfinite(float(value)) for value in (clip.start, clip.end)):
            raise ValueError(f"clip {clip.index} contains non-finite timing")
        if clip.start < previous or clip.end <= clip.start:
            raise ValueError("clip plan is not monotonic")
        if not 2 <= len(_tokens(clip.text_overlay)) <= 8:
            raise ValueError(f"invalid overlay word count at clip {clip.index}")
        previous = clip.end
    if not blueprint.music.sync_points or any(not math.isfinite(float(t)) for t in blueprint.music.sync_points):
        raise ValueError("blueprint music sync points are invalid")
    if any(t < -0.001 or t > blueprint.duration + 0.001 for t in blueprint.music.sync_points):
        raise ValueError("blueprint music sync point is outside the timeline")
    if abs(blueprint.clip_plan[-1].end - blueprint.music.sync_points[-1]) > 0.01:
        raise ValueError("clip plan does not exactly cover planned duration")

    titles = [str(title).strip() for title in blueprint.title_options if str(title).strip()]
    if len(titles) < 5:
        raise ValueError("blueprint title laboratory returned fewer than five usable titles")
    if len(set(title.lower() for title in titles)) != len(titles):
        raise ValueError("blueprint title options contain duplicates")
    if any(len(title) > 100 for title in titles):
        raise ValueError("blueprint title option exceeds the standard title limit")
    topic_tokens = set(_tokens(blueprint.core_idea.topic))
    top_title_tokens = set(_tokens(titles[0]))
    if topic_tokens and not topic_tokens.intersection(top_title_tokens):
        raise ValueError("blueprint top title is not specific to the requested topic")
    if len(str(blueprint.description).strip()) < 40:
        raise ValueError("blueprint description is too short")
    if not blueprint.hashtags:
        raise ValueError("blueprint hashtags are empty")
    if not any(set(_tokens(tag)).intersection(topic_tokens) for tag in blueprint.hashtags):
        raise ValueError("blueprint hashtags are not topic-specific")

    if not 0 <= int(blueprint.quality.score) <= 100:
        raise ValueError("blueprint quality score must be between 0 and 100")
    for key, value in blueprint.metrics.items():
        if not str(key).strip() or not math.isfinite(float(value)) or not 0 <= float(value) <= 100:
            raise ValueError(f"invalid heuristic metric: {key}")
    for hook in blueprint.hooks:
        if not all(isinstance(value, str) for value in (hook.visual, hook.text, hook.emotional)):
            raise ValueError("hook fields must be strings")
        if not math.isfinite(float(hook.score)) or not 0.0 <= float(hook.score) <= 1.0:
            raise ValueError("hook score must be between 0 and 1")
    for clip in blueprint.clip_plan:
        if not all(str(value).strip() for value in (clip.purpose, clip.emotion, clip.visual_style, clip.text_overlay, clip.camera_motion, clip.transition)):
            raise ValueError(f"clip {clip.index} contains empty creative fields")
    if not 40 <= int(blueprint.music.bpm) <= 240 or not math.isfinite(float(blueprint.music.beat_seconds)) or float(blueprint.music.beat_seconds) <= 0:
        raise ValueError("blueprint music settings are invalid")
    if not all(isinstance(tag, str) and tag.strip() for tag in blueprint.hashtags):
        raise ValueError("blueprint hashtags must be non-empty strings")
    if not all(isinstance(title, str) and title.strip() for title in blueprint.title_options):
        raise ValueError("blueprint title options must be non-empty strings")
    package_names = (
        blueprint.packaging.final_video_name,
        blueprint.packaging.thumbnail_name,
        blueprint.packaging.vertical_thumbnail_name,
        blueprint.packaging.upload_manifest_name,
    )
    if any(not isinstance(name, str) or not name.strip() or Path(name).name != name for name in package_names):
        raise ValueError("packaging artifact names must be simple filenames")
    if not isinstance(blueprint.qc.require_video, bool) or not isinstance(blueprint.qc.require_audio, bool) or not isinstance(blueprint.qc.require_independent_retention, bool):
        raise ValueError("blueprint QC flags must be booleans")
    if not blueprint.quality.passed:
        raise ValueError("blueprint failed strict editorial QC")

def blueprint_summary(blueprint: V3Blueprint) -> str:
    hook = blueprint.hooks[0].text if blueprint.hooks else ""
    return f"{blueprint.core_idea.topic} | {blueprint.edit_type} | emotion={blueprint.core_idea.target_emotion} | hook={hook!r} | clips={len(blueprint.clip_plan)} | quality={blueprint.quality.score}/100"
