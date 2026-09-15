"""Typed data models for the production editing pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Scene:
    """A semantically indexed region of source footage."""

    id: str
    start: float
    end: float
    description: str = ""
    transcript: str = ""
    objects: List[str] = field(default_factory=list)
    text: List[str] = field(default_factory=list)
    motion_score: float = 0.0
    audio_energy: float = 0.0
    brightness: float = 0.0
    face_count: int = 0
    importance_score: float = 0.0
    source: Optional[str] = None

    @property
    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))

    @property
    def searchable_text(self) -> str:
        values = [self.description, self.transcript, " ".join(self.objects), " ".join(self.text)]
        return " ".join(v for v in values if v).strip()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TimelineSegment:
    """A planned edit segment referencing a source scene."""

    id: str
    role: str
    start: float
    end: float
    source_scene_id: str
    source_start: float
    source_end: float
    label: str = "segment"
    transcript: str = ""
    caption_emphasis: List[str] = field(default_factory=list)
    effects: List[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, float(self.end) - float(self.start))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EditTimeline:
    """Validated target timeline consumed by the renderer bridge."""

    duration: float
    aspect_ratio: str = "9:16"
    segments: List[TimelineSegment] = field(default_factory=list)
    source_video: Optional[str] = None
    music_path: Optional[str] = None
    voiceover_path: Optional[str] = None
    version: int = 1

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.duration <= 0:
            errors.append("Timeline duration must be positive")
        try:
            expected_ratio = {
                "9:16": 9 / 16,
                "16:9": 16 / 9,
                "1:1": 1.0,
            }[str(self.aspect_ratio)]
        except KeyError:
            errors.append(f"Unsupported aspect ratio: {self.aspect_ratio}")
            expected_ratio = None

        previous_end = 0.0
        for index, segment in enumerate(self.segments):
            if segment.end <= segment.start:
                errors.append(f"{segment.id}: target end must be greater than start")
            if segment.source_end <= segment.source_start:
                errors.append(f"{segment.id}: source end must be greater than start")
            if segment.start < -1e-6:
                errors.append(f"{segment.id}: target start is negative")
            if segment.start > previous_end + 0.01:
                errors.append(f"{segment.id}: unfilled target gap before segment")
            if segment.start + 0.01 < previous_end:
                errors.append(f"{segment.id}: target segments overlap or are out of order")
            if segment.end > self.duration + 0.05:
                errors.append(f"{segment.id}: target end exceeds timeline duration")
            if segment.source_start < -1e-6 or segment.source_end < -1e-6:
                errors.append(f"{segment.id}: source interval is negative")
            if segment.source_end <= segment.source_start:
                errors.append(f"{segment.id}: source interval is empty")
            if index > 0 and segment.start < previous_end - 0.01:
                errors.append(f"{segment.id}: target segments overlap")
            previous_end = segment.end

        if self.segments and abs(previous_end - self.duration) > 0.05:
            errors.append("Timeline does not exactly cover its declared duration")
        if expected_ratio is not None:
            # A ratio is stored as a contract label; numeric dimensions are validated by render QC.
            if expected_ratio <= 0:
                errors.append("Timeline aspect ratio is invalid")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "duration": self.duration,
            "aspect_ratio": self.aspect_ratio,
            "source_video": self.source_video,
            "music_path": self.music_path,
            "voiceover_path": self.voiceover_path,
            "segments": [segment.to_dict() for segment in self.segments],
        }


@dataclass
class ProductionResult:
    """Machine-readable result returned by the production pipeline."""

    package_dir: str
    final_video: Optional[str] = None
    timeline_path: Optional[str] = None
    plan_path: Optional[str] = None
    script_path: Optional[str] = None
    scenes_path: Optional[str] = None
    metadata_path: Optional[str] = None
    qc_report_path: Optional[str] = None
    metrics_path: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.final_video) and not self.errors