"""Canonical production specification shared by planner, renderer and QC."""
from __future__ import annotations
from dataclasses import dataclass
from .platform_policy import PlatformPolicy,get_platform_policy

@dataclass(frozen=True)
class ProductionSpec:
    duration: float
    platform: str
    width: int
    height: int
    safe_bottom: int
    platform_policy_version: str
    max_duration: float|None

    @classmethod
    def from_values(cls,duration:float,platform:str)->"ProductionSpec":
        policy=get_platform_policy(platform)
        value=float(duration)
        if value<=0: raise ValueError("duration must be positive")
        if policy.max_seconds is not None and value>policy.max_seconds:
            raise ValueError(f"duration exceeds {platform} platform policy")
        return cls(value,policy.platform.value,policy.width,policy.height,policy.safe_bottom,policy.version,policy.max_seconds)

    def to_dict(self)->dict[str,object]:
        return {
            "duration":round(self.duration,3),
            "platform":self.platform,
            "width":self.width,
            "height":self.height,
            "safe_bottom":self.safe_bottom,
            "platform_policy_version":self.platform_policy_version,
            "max_duration":self.max_duration,
        }

__all__=["ProductionSpec"]
