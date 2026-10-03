"""Versioned platform policies used by V3 planning and packaging."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping
from enum import Enum

class PlatformId(str, Enum):
    YOUTUBE_SHORTS="youtube_shorts"
    TIKTOK="tiktok"
    INSTAGRAM_REELS="instagram_reels"
    SQUARE="square"
    YOUTUBE="youtube"

@dataclass(frozen=True)
class PlatformPolicy:
    platform: PlatformId
    version: str
    width: int
    height: int
    max_seconds: float|None
    safe_bottom: int
    cta: str
    supported_aspect_ratios: tuple[str,...]=( "16:9","9:16","1:1" )

    @property
    def aspect_ratio(self)->str:
        if self.width*16 == self.height*9: return "16:9"
        if self.width*9 == self.height*16: return "9:16"
        if self.width == self.height: return "1:1"
        return f"{self.width}:{self.height}"

    def to_dict(self)->dict[str,object]:
        return {
            "platform":self.platform.value,"policy_version":self.version,
            "width":self.width,"height":self.height,"max_seconds":self.max_seconds,
            "safe_bottom":self.safe_bottom,"cta":self.cta,
            "aspect_ratio":self.aspect_ratio,"supported_aspect_ratios":list(self.supported_aspect_ratios),
        }

POLICIES: Mapping[str,PlatformPolicy]={
    PlatformId.YOUTUBE_SHORTS.value: PlatformPolicy(PlatformId.YOUTUBE_SHORTS,"2026-10-contract-1",1080,1920,60,300,"comment",("9:16",)),
    PlatformId.TIKTOK.value: PlatformPolicy(PlatformId.TIKTOK,"2026-10-contract-1",1080,1920,180,320,"follow",("9:16",)),
    PlatformId.INSTAGRAM_REELS.value: PlatformPolicy(PlatformId.INSTAGRAM_REELS,"2026-10-contract-1",1080,1920,90,330,"share",("9:16",)),
    PlatformId.SQUARE.value: PlatformPolicy(PlatformId.SQUARE,"2026-10-contract-1",1080,1080,90,170,"share",("1:1",)),
    PlatformId.YOUTUBE.value: PlatformPolicy(PlatformId.YOUTUBE,"2026-10-contract-1",1920,1080,None,120,"subscribe",("16:9",)),
}

def get_platform_policy(platform:str|PlatformId)->PlatformPolicy:
    key=platform.value if isinstance(platform,PlatformId) else str(platform).strip().lower()
    try: return POLICIES[key]
    except KeyError as exc: raise ValueError(f"unsupported platform policy: {platform}") from exc

def platform_profiles()->dict[str,dict[str,object]]:
    return {key:dict(policy.to_dict()) for key,policy in POLICIES.items()}

__all__=["PlatformId","PlatformPolicy","POLICIES","get_platform_policy","platform_profiles"]
