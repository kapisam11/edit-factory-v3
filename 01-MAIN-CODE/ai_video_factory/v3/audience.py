"""Structured audience profile parsing for editorial planning."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class AudienceProfile:
    label: str
    age_range: tuple[int, int] | None
    interests: tuple[str, ...]
    language: str
    familiarity: str
    viewing_context: str
    tone: str
    pacing: str
    hook: str
    caption_style: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "age_range": list(self.age_range) if self.age_range else None,
            "interests": list(self.interests),
            "language": self.language,
            "familiarity": self.familiarity,
            "viewing_context": self.viewing_context,
            "tone": self.tone,
            "pacing": self.pacing,
            "hook": self.hook,
            "caption_style": self.caption_style,
        }


def parse_audience(audience: str) -> AudienceProfile:
    text = str(audience or "general short-form viewers").strip()
    lower = text.lower()
    age_match = re.search(r"(\d{1,2})\s*[-–]\s*(\d{1,2})", lower)
    age_range = None
    if age_match:
        age_range = (int(age_match.group(1)), int(age_match.group(2)))

    interests: list[str] = []
    markers = (
        "gaming", "minecraft", "fortnite", "football", "anime", "manga",
        "comedy", "history", "science", "education", "business", "finance",
        "technology", "tutorial", "storytelling",
    )
    interests.extend(marker for marker in markers if marker in lower)
    language = "de" if re.search(r"\b(german|deutsch|de)\b", lower) else "en"
    familiarity = "high" if any(x in lower for x in ("expert", "advanced", "high familiarity")) else (
        "low" if any(x in lower for x in ("beginner", "new to", "low familiarity")) else "medium"
    )
    viewing_context = "mobile" if any(x in lower for x in ("mobile", "phone", "shorts", "tiktok", "reels")) else "general"

    tone = "accessible"
    pacing = "balanced"
    hook = "curiosity_or_emotion"
    caption_style = "readable"
    if any(x in lower for x in ("comedy", "funny", "meme")):
        tone, pacing, hook, caption_style = "playful", "fast", "reaction_or_surprise", "punchy"
    elif any(x in lower for x in ("gaming", "gamer", "minecraft", "fortnite")):
        tone, pacing, hook, caption_style = "energetic", "fast", "moment_or_payoff", "high_contrast"
    elif any(x in lower for x in ("anime", "manga", "otaku")):
        tone, pacing, hook, caption_style = "dramatic", "fast", "character_or_reveal", "punchy"
    elif any(x in lower for x in ("history", "documentary", "facts", "science", "education")):
        tone, pacing, hook, caption_style = "informative", "measured", "evidence_or_question", "clear"
    elif any(x in lower for x in ("business", "finance", "entrepreneur", "marketing")):
        tone, pacing, hook, caption_style = "direct", "tight", "claim_or_result", "minimal"

    return AudienceProfile(
        label=text,
        age_range=age_range,
        interests=tuple(dict.fromkeys(interests)),
        language=language,
        familiarity=familiarity,
        viewing_context=viewing_context,
        tone=tone,
        pacing=pacing,
        hook=hook,
        caption_style=caption_style,
    )


__all__ = ["AudienceProfile", "parse_audience"]
