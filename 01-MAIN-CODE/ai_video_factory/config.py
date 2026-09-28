"""AI Video Factory configuration models and safe JSON persistence."""
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "06-CONFIG-AND-DEPLOYMENT" / "aivf_config.json"


@dataclass
class StyleProfile:
    name: str
    cuts_per_minute: float = 15.0
    zoom_intensity: float = 1.04
    avg_shot_seconds: float = 1.8
    hook_placement_seconds: float = 0.4
    preferred_filters: List[str] = field(default_factory=list)
    music_mood: str = "dramatic"
    subtitle_style: str = "bold_white"
    thumbnail_style: str = "high_contrast"
    note: str = ""


@dataclass
class PipelineConfig:
    name: str
    stages: List[str] = field(default_factory=lambda: [
        "research", "plan", "script", "thumbnail", "auto_edit",
        "voiceover", "music", "qc", "metadata", "metrics"
    ])
    description: str = ""


@dataclass
class HardwareConfig:
    encoder: str = "libx264"
    gpu_memory_mb: int = 0
    max_parallel_jobs: int = 2
    use_gpu_for_filters: bool = False


@dataclass
class APIKeys:
    groq: str = ""
    elevenlabs: str = ""
    openai: str = ""
    freesound: str = ""


@dataclass
class AIVFConfig:
    version: str = "3.0"
    active_pipeline: str = "default"
    active_style: str = "gaming_fast"
    output_root: str = "output"
    upload_root: str = "uploads"
    asset_root: str = "assets"
    templates_root: str = "templates"
    knowledge_root: str = "knowledge_base_v3"

    pipelines: Dict[str, PipelineConfig] = field(default_factory=lambda: {
        "default": PipelineConfig("default", description="Full pipeline with everything"),
        "fast": PipelineConfig("fast", stages=["plan", "script", "auto_edit", "metadata"],
                               description="Skip research and extras for speed"),
        "package_only": PipelineConfig("package_only",
                                       stages=["research", "plan", "script", "thumbnail", "metadata"],
                                       description="Generate plan + script, no video editing"),
    })

    style_profiles: Dict[str, StyleProfile] = field(default_factory=lambda: {
        "gaming_fast": StyleProfile(name="gaming_fast", cuts_per_minute=20.0, zoom_intensity=1.1,
                                    avg_shot_seconds=1.5,
                                    preferred_filters=["jump_cut", "impact_frame", "speed_ramp"],
                                    music_mood="intense", subtitle_style="bold_yellow",
                                    thumbnail_style="high_contrast"),
        "gaming_cinematic": StyleProfile(name="gaming_cinematic", cuts_per_minute=8.0,
                                         avg_shot_seconds=2.5,
                                         preferred_filters=["cinematic_transition", "soft_settle", "camera_move"],
                                         music_mood="emotional", subtitle_style="elegant_white",
                                         thumbnail_style="cinematic"),
        "tutorial": StyleProfile(name="tutorial", cuts_per_minute=10.0,
                                 preferred_filters=["zoom", "cinematic_transition"],
                                 music_mood="calm", subtitle_style="clear_white",
                                 thumbnail_style="clean_text"),
    })

    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    api_keys: APIKeys = field(default_factory=APIKeys)
    heuristics: Dict[str, Any] = field(default_factory=lambda: {
        "viral_length_range": [30, 60],
        "optimal_length_seconds": 42,
        "cuts_per_minute": 15.0,
        "avg_shot_seconds": 1.8,
        "hook_placement_seconds": [0.3, 0.5],
        "climax_position_pct": [0.60, 0.75],
    })

    def validate(self) -> None:
        if not str(self.version).strip():
            raise ValueError("config version is required")
        if not self.pipelines or self.active_pipeline not in self.pipelines:
            raise ValueError("active_pipeline must reference a configured pipeline")
        if not self.style_profiles or self.active_style not in self.style_profiles:
            raise ValueError("active_style must reference a configured style profile")
        if self.hardware.max_parallel_jobs < 1 or self.hardware.max_parallel_jobs > 8:
            raise ValueError("hardware.max_parallel_jobs must be between 1 and 8")
        if self.hardware.gpu_memory_mb < 0:
            raise ValueError("hardware.gpu_memory_mb cannot be negative")
        for name, pipeline in self.pipelines.items():
            if not str(name).strip() or not pipeline.stages:
                raise ValueError("configured pipelines require a name and at least one stage")
        for name, profile in self.style_profiles.items():
            if not str(name).strip() or profile.cuts_per_minute <= 0 or profile.avg_shot_seconds <= 0:
                raise ValueError("style profiles require positive pacing values")
        heuristic = self.heuristics
        if not isinstance(heuristic, dict):
            raise ValueError("heuristics must be an object")
        if "optimal_length_seconds" in heuristic and float(heuristic["optimal_length_seconds"]) <= 0:
            raise ValueError("optimal_length_seconds must be positive")
        if any(not str(value).strip() for value in (self.output_root, self.upload_root, self.asset_root, self.templates_root, self.knowledge_root)):
            raise ValueError("configured runtime paths cannot be empty")
    def to_dict(self, redact_secrets: bool = False) -> dict:
        data = asdict(self)
        if redact_secrets:
            data["api_keys"] = {key: "" for key in data.get("api_keys", {})}
        return data

    def save(self, path: str = str(_DEFAULT_CONFIG_PATH)) -> None:
        """Persist configuration atomically without writing API credentials."""
        self.validate()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".aivf-config-", suffix=".partial", dir=str(target.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(self.to_dict(redact_secrets=True), handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str = str(_DEFAULT_CONFIG_PATH)) -> "AIVFConfig":
        if not os.path.exists(path):
            config = cls()
            config.save(path)
            return config

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("Configuration root must be a JSON object")

        data["hardware"] = HardwareConfig(**(data.get("hardware") or {}))
        data["api_keys"] = APIKeys(**(data.get("api_keys") or {}))
        data["pipelines"] = {
            name: PipelineConfig(**value) if isinstance(value, dict) else PipelineConfig(name=name)
            for name, value in (data.get("pipelines") or {}).items()
        }
        data["style_profiles"] = {
            name: StyleProfile(**value) if isinstance(value, dict) else StyleProfile(name=name)
            for name, value in (data.get("style_profiles") or {}).items()
        }
        config = cls(**data)
        config.validate()
        return config

    def get_pipeline(self, name: Optional[str] = None) -> PipelineConfig:
        return self.pipelines.get(name or self.active_pipeline, self.pipelines["default"])

    def get_style(self, name: Optional[str] = None) -> StyleProfile:
        return self.style_profiles.get(name or self.active_style, self.style_profiles["gaming_fast"])

    def set_api_key(self, provider: str, key: str) -> None:
        """Set an API key for the current process without persisting it."""
        key = str(key or "")
        env_names = {
            "groq": ("groq", "GROQ_API_KEY"),
            "elevenlabs": ("elevenlabs", "ELEVENLABS_API_KEY"),
            "openai": ("openai", "OPENAI_API_KEY"),
            "freesound": ("freesound", "FREESOUND_API_KEY"),
        }
        if provider not in env_names:
            raise ValueError(f"Unsupported API provider: {provider}")
        attr, env_name = env_names[provider]
        setattr(self.api_keys, attr, key)
        os.environ[env_name] = key

    def apply_heuristics(self, heuristics: Dict[str, Any]) -> None:
        self.heuristics.update(heuristics)
        self.save()
