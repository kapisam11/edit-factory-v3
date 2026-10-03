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
from .v3_scoring import heuristic_metrics
from .v3.migrations import migrate_to_current
from .v3.audience import parse_audience


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
    migration_history: Sequence[Mapping[str, Any]] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "hooks", tuple(self.hooks))
        object.__setattr__(self, "clip_plan", tuple(self.clip_plan))
        object.__setattr__(self, "retention_map", tuple(self.retention_map))
        object.__setattr__(self, "title_options", tuple(self.title_options))
        object.__setattr__(self, "hashtags", tuple(self.hashtags))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "editorial_decisions", tuple(self.editorial_decisions))
        object.__setattr__(
            self,
            "migration_history",
            tuple(dict(item) for item in self.migration_history),
        )
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

    def to_dict(self) -> dict[str, Any]:
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
            "migration_history": [dict(item) for item in self.migration_history],
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
                migration_history=tuple(
                    dict(item) for item in payload.get("migration_history", ())
                    if isinstance(item, Mapping)
                ),
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


PLATFORM_PROFILES: Mapping[str, dict[str, Any]] = {
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

EDIT_STRATEGIES: Mapping[EditType, dict[str, Any]] = {
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

def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text).lower())

def _normalize_sentence(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip()).strip(" .,-")
    return value + "." if value else ""

def analyze_core_idea(topic: str, context: str = "", audience: str = "general short-form viewers") -> CoreIdea:
    from .v3.planning import analyze_core_idea as _impl
    return _impl(topic, context, audience)


def choose_edit_type(core: CoreIdea, requested: str | None = None) -> EditType:
    from .v3.planning import choose_edit_type as _impl
    return _impl(core, requested)


def generate_hooks(core: CoreIdea, edit_type: EditType, *, source_evidence: Mapping[str, Any] | None = None) -> list[HookPack]:
    from .v3.planning import generate_hooks as _impl
    return _impl(core, edit_type, source_evidence=source_evidence)


def _purpose_sequence(count: int, strategy: Mapping[str, Any]) -> list[str]:
    values = list(strategy["purposes"])
    while len(values) < count:
        values.insert(max(3, len(values) - 2), "Escalation")
    return values[:count]


def _allocate_durations(count: int, total: float, minimum: float, maximum: float) -> list[float]:
    from .v3.planning import allocate_durations as _impl
    return _impl(count, total, minimum, maximum)


def _overlay_for_purpose(purpose: str, core: CoreIdea, max_words: int, edit_type: EditType) -> str:
    from .v3.planning import overlay_for_purpose as _impl
    return _impl(purpose, core, max_words, edit_type)


def _unique_overlay(purpose: str, core: CoreIdea, max_words: int, index: int, used: set[str], edit_type: EditType) -> str:
    from .v3.planning import unique_overlay as _impl
    return _impl(purpose, core, max_words, index, used, edit_type)


def build_clip_plan(core: CoreIdea, edit_type: EditType, config: V3Config) -> list[ClipBeat]:
    from .v3.planning import build_clip_plan as _impl
    return _impl(core, edit_type, config)


def analyze_music(core: CoreIdea, config: V3Config, clips: Sequence[ClipBeat]) -> MusicPlan:
    from .v3.planning import analyze_music as _impl
    return _impl(core, config, clips)


def build_retention_map(config: V3Config, clips: Sequence[ClipBeat], music: MusicPlan) -> list[RetentionEvent]:
    from .v3.planning import build_retention_map as _impl
    return _impl(config, clips, music)


def _heuristic_metrics(core: CoreIdea, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], quality: QualityReport) -> dict[str, float]:
    from .v3.planning import heuristic_metrics_for_plan as _impl
    return _impl(core, hooks, clips, quality)


def _planned_metadata(core: CoreIdea, audience_profile: Any | None = None) -> tuple[str, list[str], list[str], str]:
    from .v3.planning import _planned_metadata as _impl
    return _impl(core, audience_profile)


# Backward-compatible aliases; semantic use of this helper is intentionally avoided.
def _compact(text: str, max_words: int = 6) -> str:
    from .v3.planning import compact_overlay_text
    return compact_overlay_text(text, max_words)


def _metadata(core: CoreIdea) -> tuple[str, list[str], list[str], str]:
    return _planned_metadata(core)


def create_v3_blueprint(
    topic: str,
    *,
    context: str = "",
    config: V3Config | None = None,
    edit_type: str | None = None,
    footage_evidence: Mapping[str, Any] | None = None,
) -> V3Blueprint:
    from .v3.planning import create_blueprint
    return create_blueprint(
        topic,
        context=context,
        config=config,
        edit_type=edit_type,
        footage_evidence=footage_evidence,
    )

def platform_variants(config: V3Config) -> dict[str, dict[str, Any]]:
    config.validate()
    return {key: dict(value) for key, value in PLATFORM_PROFILES.items()}

def automated_editorial_checks(
    core: CoreIdea,
    edit_type: EditType,
    hooks: Sequence[HookPack],
    clips: Sequence[ClipBeat],
    retention: Sequence[RetentionEvent],
    config: V3Config,
) -> QualityReport:
    from .v3.assessment import automated_editorial_checks as _impl
    return _impl(core, edit_type, hooks, clips, retention, config)

def validate_blueprint(blueprint: V3Blueprint) -> None:
    from .v3.validation import validate_blueprint as _impl
    return _impl(blueprint)

def blueprint_summary(blueprint: V3Blueprint) -> str:
    hook = blueprint.hooks[0].text if blueprint.hooks else ""
    return f"{blueprint.core_idea.topic} | {blueprint.edit_type} | emotion={blueprint.core_idea.target_emotion} | hook={hook!r} | clips={len(blueprint.clip_plan)} | quality={blueprint.quality.score}/100"
