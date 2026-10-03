"""Edit Factory v3 creative planning, semantic analysis, retention and QC."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence
from types import MappingProxyType

from .v3_retention import evaluate_retention_editorial_fit
from .v3_scores import V3ScoreBundle
from .editorial_evaluation import EditorialDecision, Evidence, build_retention_decisions, summarize_editorial_evidence
from .idempotency import stable_hash
from .v3.platform_policy import POLICIES
from .v3.production_spec import ProductionSpec
from .v3.hook_eval import evaluate_hook_candidates, generate_hook_candidates
from .v3.schema import validate_blueprint_payload
from .v3.migrations import migrate_to_current
from .v3.audience import parse_audience
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
    platform: str | Platform = "youtube_shorts"
    audience: str = "general short-form viewers"
    bpm: int = 120
    retention_interval: float = 2.0
    max_overlay_words: int = 6
    min_clip_seconds: float = 0.8
    max_clip_seconds: float = 4.0
    language: str = "en"
    seed: int = 0

    def validate(self) -> None:
        numeric = (
            ("target_seconds", self.target_seconds, (int, float)),
            ("bpm", self.bpm, (int,)),
            ("retention_interval", self.retention_interval, (int, float)),
            ("max_overlay_words", self.max_overlay_words, (int,)),
            ("min_clip_seconds", self.min_clip_seconds, (int, float)),
            ("max_clip_seconds", self.max_clip_seconds, (int, float)),
            ("seed", self.seed, (int,)),
        )
        for name, value, allowed in numeric:
            if isinstance(value, bool) or not isinstance(value, allowed):
                raise TypeError(f"{name} has the wrong type")
        target = float(self.target_seconds)
        bpm = int(self.bpm)
        retention = float(self.retention_interval)
        max_words = int(self.max_overlay_words)
        minimum = float(self.min_clip_seconds)
        maximum = float(self.max_clip_seconds)
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
        if self.language.strip().lower() not in {"en"}:
            raise ValueError("unsupported planning language; only en is implemented")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError("seed must be an integer")
        ProductionSpec.from_values(target, platform_value)


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
    evaluation: Mapping[str, Any] = field(default_factory=dict)
    score_semantics: str = "editorial_heuristic_score"


@dataclass(frozen=True)
class ClipEvidence:
    source_asset: str = ""
    source_start: float = 0.0
    source_end: float = 0.0
    semantic_tags: Sequence[str] = field(default_factory=tuple)
    evidence_score: float = 0.0
    evidence_status: str = "planned"

    def __post_init__(self) -> None:
        score = float(self.evidence_score)
        if not 0.0 <= score <= 1.0:
            raise ValueError("clip evidence score must be between 0 and 1")
        if self.source_end < self.source_start:
            raise ValueError("clip evidence boundaries are invalid")
        object.__setattr__(self, "semantic_tags", tuple(str(value) for value in self.semantic_tags))


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
    evidence: ClipEvidence = field(default_factory=ClipEvidence)


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
    reason: str = "ATTENTION_RISK"
    semantic_importance: float = 0.5
    confidence: float = 0.5


@dataclass(frozen=True)
class PlatformProfile:
    width: int
    height: int
    max_seconds: float | None
    safe_bottom: int
    cta: str
    policy_version: str = "2026-10-contract-1"


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
        if not isinstance(self.passed, bool):
            raise TypeError("quality.passed must be a boolean")
        if isinstance(self.score, bool) or not isinstance(self.score, int):
            raise TypeError("quality.score must be an integer")
        if not isinstance(self.checks, Mapping):
            raise TypeError("quality.checks must be an object")
        if any(not isinstance(value, bool) for value in self.checks.values()):
            raise TypeError("quality check values must be booleans")
        object.__setattr__(self, "checks", MappingProxyType({str(k): value for k, value in self.checks.items()}))
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
    score_bundle: V3ScoreBundle = field(
        default_factory=lambda: V3ScoreBundle(
            technical_validity=None,
            creative_quality=0.0,
            performance_heuristic=0.0,
        )
    )
    editorial_decisions: Sequence[EditorialDecision] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "hooks", tuple(self.hooks))
        object.__setattr__(self, "clip_plan", tuple(self.clip_plan))
        object.__setattr__(self, "retention_map", tuple(self.retention_map))
        object.__setattr__(self, "title_options", tuple(self.title_options))
        object.__setattr__(self, "hashtags", tuple(self.hashtags))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "editorial_decisions", tuple(self.editorial_decisions))
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
    def platform_constraints(self) -> PlatformProfile:
        profile = self.platform_profile
        if profile is None:
            raise ValueError("blueprint platform profile is missing")
        return profile
    @property
    def schema_version(self) -> str:
        from .v3.schema import CURRENT_SCHEMA_VERSION
        return CURRENT_SCHEMA_VERSION

    @property
    def duration(self) -> float:
        return float(self.clip_plan[-1].end) if self.clip_plan else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "schema_version": self.schema_version,
            "platform": self.platform,
            "audience": self.audience,
            "platform_profile": asdict(self.platform_constraints),
            "qc": asdict(self.qc),
            "packaging": asdict(self.packaging),
            "metric_metadata": asdict(self.metric_metadata),
            "score_bundle": self.score_bundle.to_dict(),
            "editorial_decisions": [item.to_dict() for item in self.editorial_decisions],
            "editorial_evidence_summary": summarize_editorial_evidence(self.editorial_decisions),
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
        payload = migrate_to_current(payload)
        payload = validate_blueprint_payload(payload)
        try:
            core = CoreIdea(**dict(payload["core_idea"]))
            hooks = tuple(
                HookPack(
                    visual=str(item["visual"]),
                    text=str(item["text"]),
                    emotional=str(item["emotional"]),
                    score=float(item["score"]),
                    evaluation=dict(item.get("evaluation") or {}),
                    score_semantics=str(item.get("score_semantics", "legacy_template_priority")),
                )
                for item in payload["hooks"]
            )
            clips = tuple(
                ClipBeat(
                    index=int(item["index"]),
                    start=float(item["start"]),
                    end=float(item["end"]),
                    purpose=str(item["purpose"]),
                    emotion=str(item["emotion"]),
                    visual_style=str(item["visual_style"]),
                    text_overlay=str(item["text_overlay"]),
                    camera_motion=str(item["camera_motion"]),
                    transition=str(item["transition"]),
                    evidence=ClipEvidence(**dict(item.get("evidence") or {})),
                )
                for item in payload["clip_plan"]
            )
            music_data = dict(payload["music"])
            music_data["sync_points"] = tuple(float(x) for x in music_data["sync_points"])
            music = MusicPlan(**music_data)
            retention = tuple(RetentionEvent(**dict(item)) for item in payload["retention_map"])
            quality = QualityReport(**dict(payload["quality"]))
            platform_name = str(payload.get("platform") or "youtube_shorts").strip().lower()
            raw_platform = payload.get("platform_profile")
            profile = (
                PlatformProfile(**dict(raw_platform))
                if isinstance(raw_platform, Mapping)
                else PlatformProfile(**PLATFORM_PROFILES[platform_name])
            )
            qc = QCRequirements(**dict(payload.get("qc") or {}))
            packaging = PackagingPlan(**dict(payload.get("packaging") or {}))
            metric_metadata = MetricMetadata(**dict(payload.get("metric_metadata") or {}))
            raw_bundle = payload.get("score_bundle")
            score_bundle = (
                V3ScoreBundle(**dict(raw_bundle))
                if isinstance(raw_bundle, Mapping)
                else V3ScoreBundle(
                    technical_validity=None,
                    creative_quality=quality.score,
                    performance_heuristic=0.0,
                )
            )
            editorial_decisions = tuple(
                EditorialDecision(
                    operation=str(item["operation"]),
                    timestamp=float(item["timestamp"]),
                    reason=str(item["reason"]),
                    confidence=float(item["confidence"]),
                    source=str(item.get("source", "v3")),
                    decision_version=str(item.get("decision_version", "1.0.0")),
                    policy_version=str(item.get("policy_version", "1.0.0")),
                    config_hash=str(item.get("config_hash", "")),
                    source_hash=str(item.get("source_hash", "")),
                    planner_version=str(item.get("planner_version", "3.0.0")),
                    evidence=tuple(
                        Evidence(
                            kind=str(evidence.get("kind", "heuristic")),
                            method=str(evidence.get("method", "unknown")),
                            confidence=float(evidence.get("confidence", item["confidence"])),
                            version=str(evidence.get("version", "unknown")),
                            details=dict(evidence.get("details") or {}),
                        )
                        for evidence in item.get("evidence", ())
                    ),
                )
                for item in payload.get("editorial_decisions", ())
            )
            restored = cls(
                version=str(payload["version"]),
                core_idea=core,
                edit_type=str(payload["edit_type"]),
                hooks=hooks,
                clip_plan=clips,
                music=music,
                retention_map=retention,
                thumbnail_concept=str(payload["thumbnail_concept"]),
                title_options=tuple(str(x) for x in payload["title_options"]),
                hashtags=tuple(str(x) for x in payload["hashtags"]),
                description=str(payload["description"]),
                platform_variants=dict(payload["platform_variants"]),
                quality=quality,
                metrics=dict(payload["metrics"]),
                capabilities=tuple(str(x) for x in payload["capabilities"]),
                platform=platform_name,
                audience=str(payload.get("audience") or "general short-form viewers"),
                platform_profile=profile,
                qc=qc,
                packaging=packaging,
                metric_metadata=metric_metadata,
                score_bundle=score_bundle,
                editorial_decisions=editorial_decisions,
            )
            validate_blueprint(restored)
            return restored
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError(f"invalid V3 blueprint payload: {exc}") from exc

def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("platform_variants must be an object")
    frozen: dict[str, Mapping[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(item, Mapping):
            raise ValueError("platform variant must be an object")
        frozen[str(key)] = MappingProxyType(dict(item))
    return MappingProxyType(frozen)


PLATFORM_PROFILES: Mapping[str, Dict[str, Any]] = {
    key: {
        "width": policy.width,
        "height": policy.height,
        "max_seconds": policy.max_seconds,
        "safe_bottom": policy.safe_bottom,
        "cta": policy.cta,
        "policy_version": policy.version,
    }
    for key, policy in POLICIES.items()
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
    audience_profile = parse_audience(audience)
    interests = set(audience_profile.interests)
    if "comedy" in interests:
        scores["funny"] += 0.05
    if {"history", "science", "education"} & interests:
        scores["curious"] += 0.05
    if {"gaming", "minecraft", "fortnite"} & interests:
        scores["dramatic"] += 0.02
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

def generate_hooks(core: CoreIdea, edit_type: EditType, *, source_evidence: Mapping[str, Any] | None = None) -> list[HookPack]:
    evaluated = evaluate_hook_candidates(
        generate_hook_candidates(
            core.topic,
            core.stakes,
            core.emotional_angle,
            core.watch_to_end_reason,
            edit_type.value,
        ),
        core.topic,
        core.stakes,
        core.emotional_angle,
        core.watch_to_end_reason,
        source_evidence=source_evidence,
    )
    return [
        HookPack(
            candidate.visual,
            candidate.text,
            candidate.emotional_reason,
            evaluation.score,
            evaluation=evaluation.to_dict(),
            score_semantics="editorial_heuristic_score",
        )
        for candidate, evaluation in evaluated
    ]
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
    """Create only evidence-backed semantic retention enhancements."""
    config.validate()
    if not clips:
        return []
    purpose_kind = {
        "Hook": ("zoom", "HOOK_ESTABLISHMENT", 0.80),
        "Setup": ("motion", "STORY_SETUP", 0.70),
        "Build": ("motion", "ESCALATION", 0.80),
        "Conflict": ("motion", "CONFLICT_EMPHASIS", 0.82),
        "Climax": ("beat drop", "PAYOFF_ALIGNMENT", 0.94),
        "Payoff": ("beat drop", "PAYOFF_ALIGNMENT", 0.96),
        "Punchline": ("beat drop", "PAYOFF_ALIGNMENT", 0.94),
        "Reaction": ("angle", "REACTION_EMPHASIS", 0.78),
        "Context": ("motion", "CONTEXT_SUPPORT", 0.65),
        "Question": ("text", "COMPREHENSION_SUPPORT", 0.70),
        "Claim": ("text", "CLAIM_EMPHASIS", 0.70),
        "Curiosity": ("text", "COMPREHENSION_SUPPORT", 0.70),
        "Threat": ("zoom", "THREAT_EMPHASIS", 0.75),
        "Escalation": ("motion", "ESCALATION", 0.80),
        "Evidence": ("text", "EVIDENCE_SUPPORT", 0.75),
        "Proof": ("text", "EVIDENCE_SUPPORT", 0.75),
        "Memory": ("motion", "MEMORY_EMPHASIS", 0.68),
        "Contrast": ("angle", "CONTRAST_EMPHASIS", 0.72),
        "Trait": ("angle", "TRAIT_EMPHASIS", 0.72),
        "Final impact": ("text", "FINAL_IMPACT", 0.86),
    }
    events: list[RetentionEvent] = []

    def add_event(time_value: float, kind: str, reason: str, confidence: float) -> None:
        timestamp = round(max(0.0, min(float(time_value), config.target_seconds - 0.01)), 3)
        close_indexes = [
            index
            for index, existing in enumerate(events)
            if abs(existing.time - timestamp) < 0.35
        ]
        if close_indexes:
            strongest_index = max(close_indexes, key=lambda index: events[index].confidence)
            if confidence <= events[strongest_index].confidence:
                return
            for index in reversed(close_indexes):
                events.pop(index)
        events.append(
            RetentionEvent(
                timestamp,
                kind,
                f"{reason.replace('_', ' ').capitalize()}: apply a restrained visual treatment while preserving story continuity.",
                reason,
                min(1.0, confidence),
                min(1.0, confidence),
            )
        )

    first = clips[0]
    kind, reason, confidence = purpose_kind.get(first.purpose, ("zoom", "HOOK_ESTABLISHMENT", 0.76))
    add_event(first.start, kind, reason, confidence)

    for clip in clips[1:]:
        mapped = purpose_kind.get(clip.purpose)
        if mapped is None:
            continue
        kind, reason, confidence = mapped
        add_event(clip.start, kind, reason, confidence)

    payoff_candidates = [
        clip.start
        for clip in clips
        if clip.purpose in {"Climax", "Payoff", "Punchline", "Final impact"}
    ]
    if payoff_candidates:
        nearest = min(payoff_candidates, key=lambda value: abs(value - music.drop_time))
        if abs(nearest - music.drop_time) <= max(0.75, config.retention_interval * 0.5):
            add_event(nearest, "beat drop", "PAYOFF_ALIGNMENT", 0.96)

    final_clip = clips[-1]
    if final_clip.purpose in {"Final impact", "Payoff", "Reaction"} and not any(
        abs(event.time - final_clip.start) < 0.35 for event in events
    ):
        add_event(final_clip.start, "text", "FINAL_IMPACT", 0.86)

    return sorted(events, key=lambda event: event.time)
def platform_variants(config: V3Config) -> dict[str, dict[str, Any]]:
    config.validate()
    return {key: dict(value) for key, value in PLATFORM_PROFILES.items()}

def automated_editorial_checks(core: CoreIdea, edit_type: EditType, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], retention: Sequence[RetentionEvent], config: V3Config) -> QualityReport:
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
        # Retention is judged by semantic anchors, not a fixed edit clock.
        # The independent retention report remains responsible for cadence/spacing diagnostics.
        "retention_semantic_coverage": bool(retention) and (
            any(event.time <= 0.35 for event in retention)
            and any(event.reason in {"PAYOFF_ALIGNMENT", "FINAL_IMPACT"} for event in retention)
        ),
        "has_payoff": any(c.purpose in {"Payoff","Punchline","Climax"} for c in clips),
        "has_final_impact": bool(clips and clips[-1].purpose in {"Final impact","Payoff","Reaction"}),
        "hook_payoff_continuity": bool(clips and clips[0].emotion == clips[-1].emotion),
    }
    warnings = []
    for key, message in {
        "hook_under_two_seconds":"hook exceeds preferred opening window",
        "no_duplicate_overlays":"repeated overlay text detected",
        "retention_semantic_coverage":"retention map is missing an opening or payoff anchor",
        "hook_payoff_continuity":"hook and payoff emotion differ",
    }.items():
        if not checks[key]:
            warnings.append(message)
    retention_report = evaluate_retention_editorial_fit(
        [asdict(event) for event in retention],
        duration=config.target_seconds,
        target_interval=config.retention_interval,
        clip_boundaries=[clip.start for clip in clips[1:]],
    )
    checks["retention_editorial_fit"] = retention_report.passed
    warnings.extend(f"retention: {warning}" for warning in retention_report.warnings)
    score = round(100 * sum(checks.values()) / len(checks)) if checks else 0
    return QualityReport(
        score >= 95 and all(
            checks[k]
            for k in ("emotion_defined", "single_edit_type_strategy", "duration_bounds", "exact_duration", "has_payoff")
        ),
        score,
        checks,
        warnings,
    )

def _heuristic_metrics(core: CoreIdea, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], quality: QualityReport) -> dict[str, float]:
    hook = hooks[0].score if hooks else 0.0
    avg = clips[-1].end / len(clips) if clips else 0.0
    pace = min(1.0, 2.8 / max(avg, 0.1))
    q = quality.score / 100.0
    emotion = 0.9 if core.target_emotion in {"trust","dramatic","inspiring","nostalgic"} else 0.82
    return heuristic_metrics(hook=hook, pace=pace, quality=q, emotion=emotion)


def _planned_metadata(core: CoreIdea, audience_profile: Any | None = None) -> tuple[str, list[str], list[str], str]:
    """Build provisional blueprint metadata; final upload metadata is manifest-derived."""
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

def create_v3_blueprint(topic: str, *, context: str = "", config: V3Config | None = None, edit_type: str | None = None, footage_evidence: Mapping[str, Any] | None = None) -> V3Blueprint:
    cfg = config or V3Config()
    cfg.validate()
    core = analyze_core_idea(topic, context, cfg.audience)
    selected = choose_edit_type(core, edit_type)
    evidence_context = dict(footage_evidence or {})
    evidence_context["audience"] = parse_audience(cfg.audience).to_dict()
    hooks = generate_hooks(core, selected, source_evidence=evidence_context)
    clips = build_clip_plan(core, selected, cfg)
    if footage_evidence:
        from .v3.clip_evidence import build_clip_evidence
        evidence_rows = build_clip_evidence(
            [asdict(item) for item in clips],
            dict(footage_evidence),
            source_asset=str(dict(footage_evidence).get("source_asset", "")),
        )
        clip_by_index = {int(row.get("clip_index", 0)): row for row in evidence_rows}
        enriched: list[ClipBeat] = []
        for clip in clips:
            row = clip_by_index.get(clip.index, {})
            enriched.append(
                ClipBeat(
                    clip.index, clip.start, clip.end, clip.purpose, clip.emotion,
                    clip.visual_style, clip.text_overlay, clip.camera_motion,
                    clip.transition,
                    ClipEvidence(
                        source_asset=str(row.get("source_asset", "")),
                        source_start=float(row.get("source_start", 0.0)),
                        source_end=float(row.get("source_end", 0.0)),
                        semantic_tags=tuple(str(x) for x in row.get("semantic_tags", ())),
                        evidence_score=float(row.get("evidence_score", 0.0)),
                        evidence_status=str(row.get("status", "unsupported")),
                    ),
                )
            )
        clips = enriched
    music = analyze_music(core, cfg, clips)
    retention = build_retention_map(cfg, clips, music)
    thumbnail, titles, tags, description = _planned_metadata(core, parse_audience(cfg.audience))
    quality = automated_editorial_checks(core, selected, hooks, clips, retention, cfg)
    metrics = _heuristic_metrics(core, hooks, clips, quality)
    clip_purposes = {round(clip.start, 3): clip.purpose for clip in clips}
    config_hash = stable_hash(asdict(cfg))
    editorial_decisions = build_retention_decisions(
        [asdict(event) for event in retention],
        clip_purposes=clip_purposes,
        config_hash=config_hash,
        policy_version="2.0.0-semantic",
        planner_version="3.0.0",
    )
    score_bundle = V3ScoreBundle.from_blueprint(
        technical_validity=None,
        creative_quality=float(quality.score),
        metrics=metrics,
    )
    platform_value = cfg.platform.value if isinstance(cfg.platform, Platform) else str(cfg.platform).strip().lower()
    blueprint = V3Blueprint(
        "3.0.0",
        core,
        selected.value,
        hooks,
        clips,
        music,
        retention,
        thumbnail,
        titles,
        tags,
        description,
        platform_variants(cfg),
        quality,
        metrics,
        list(V3_CAPABILITIES),
        platform=platform_value,
        audience=cfg.audience,
        score_bundle=score_bundle,
        editorial_decisions=editorial_decisions,
    )
    validate_blueprint(blueprint)
    return blueprint

def validate_blueprint(blueprint: V3Blueprint) -> None:
    if not isinstance(blueprint, V3Blueprint):
        raise ValueError("expected a V3Blueprint instance")
    if blueprint.version != "3.0.0":
        raise ValueError("unexpected blueprint version")
    from .v3_capabilities import REQUIRED_CAPABILITIES
    available_capabilities = set(blueprint.capabilities)
    missing_capabilities = sorted(REQUIRED_CAPABILITIES - available_capabilities)
    if missing_capabilities:
        raise ValueError("v3 blueprint is missing required capabilities: " + ", ".join(missing_capabilities))
    if len(available_capabilities) != len(blueprint.capabilities):
        raise ValueError("v3 blueprint capabilities must be unique")
    if blueprint.platform not in PLATFORM_PROFILES:
        raise ValueError(f"unsupported blueprint platform: {blueprint.platform}")
    expected_profile = PLATFORM_PROFILES[blueprint.platform]
    if asdict(blueprint.platform_constraints) != dict(expected_profile):
        raise ValueError("blueprint platform profile does not match registered platform constraints")
    if not 0.01 <= float(blueprint.qc.target_tolerance_seconds) <= 1.0:
        raise ValueError("blueprint QC target tolerance is invalid")
    if blueprint.metric_metadata.method != "heuristic" or blueprint.metric_metadata.confidence not in {"low", "medium", "high"}:
        raise ValueError("blueprint metric metadata is invalid")
    if blueprint.metric_metadata.calibration_status not in {"uncalibrated", "calibrated"}:
        raise ValueError("blueprint metric calibration status is invalid")
    for score_name, score_value in (
        ("technical_validity", blueprint.score_bundle.technical_validity),
        ("creative_quality", blueprint.score_bundle.creative_quality),
        ("performance_heuristic", blueprint.score_bundle.performance_heuristic),
    ):
        if score_value is None:
            if score_name != "technical_validity":
                raise ValueError(f"blueprint {score_name} cannot be null")
            continue
        if not math.isfinite(float(score_value)) or not 0.0 <= float(score_value) <= 100.0:
            raise ValueError(f"blueprint {score_name} is invalid")

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
    previous_sync = -1e-6
    for sync_point in blueprint.music.sync_points:
        if sync_point < previous_sync:
            raise ValueError("blueprint music sync points are not monotonic")
        if sync_point < -0.001 or sync_point > blueprint.duration + 0.001:
            raise ValueError("blueprint music sync point is outside the timeline")
        previous_sync = sync_point
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
    for hook in blueprint.hooks:
        if hook.score_semantics not in {"editorial_heuristic_score", "legacy_template_priority"}:
            raise ValueError("hook score semantics are invalid")
        if not hook.evaluation or not hook.evaluation.get("evaluator"):
            raise ValueError("hook evaluation evidence is missing")
    for clip in blueprint.clip_plan:
        if clip.evidence.evidence_status not in {"planned", "supported", "weak", "unsupported"}:
            raise ValueError(f"clip {clip.index} has invalid evidence status")
        if clip.evidence.evidence_status == "unsupported" and clip.purpose in {"Hook", "Payoff", "Punchline", "Climax", "Final impact"}:
            raise ValueError(f"critical clip {clip.index} has no supporting source evidence")

    if not blueprint.quality.passed:
        failed_checks = [
            name
            for name, passed in blueprint.quality.checks.items()
            if not passed
        ]
        detail = ", ".join(failed_checks) or "quality threshold"
        warnings = "; ".join(str(item) for item in blueprint.quality.warnings)
        suffix = f"; warnings={warnings}" if warnings else ""
        raise ValueError(
            f"blueprint failed strict editorial QC (score={blueprint.quality.score}; "
            f"failed={detail}{suffix})"
        )

def blueprint_summary(blueprint: V3Blueprint) -> str:
    hook = blueprint.hooks[0].text if blueprint.hooks else ""
    return f"{blueprint.core_idea.topic} | {blueprint.edit_type} | emotion={blueprint.core_idea.target_emotion} | hook={hook!r} | clips={len(blueprint.clip_plan)} | quality={blueprint.quality.score}/100"
