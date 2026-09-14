"""AI Video Factory configuration models and safe JSON persistence."""
import json
import os
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
    version: str = "2.0"
    active_pipeline: str = "default"
    active_style: str = "gaming_fast"
    output_root: str = "output"
    upload_root: str = "uploads"
    asset_root: str = "assets"
    templates_root: str = "templates"
    knowledge_root: str = "knowledge_base_v2"

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

    def to_dict(self, redact_secrets: bool = False) -> dict:
        data = asdict(self)
        if redact_secrets:
            data["api_keys"] = {key: "" for key in data.get("api_keys", {})}
        return data

    def save(self, path: str = str(_DEFAULT_CONFIG_PATH)) -> None:
        """Persist configuration without writing API credentials to disk."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(redact_secrets=True), f, indent=2)

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
        return cls(**data)

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
