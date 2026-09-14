"""Text-to-speech with multiple backends.

Backends (in order of quality):
1. Edge TTS (free, natural, no API key) ← DEFAULT
2. ElevenLabs (best quality, requires API key)
3. pyttsx3 (offline, robotic, fallback)

Usage:
    from ai_video_factory.tts import generate_voiceover
    generate_voiceover("Nobody expected him to survive...", "vo.mp3")
"""
import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# Edge TTS voice presets
EDGE_VOICES = {
    "deep_documentary": "en-US-GuyNeural",      # Deep male narrator
    "emotional": "en-US-DavisNeural",            # Expressive male
    "energetic": "en-US-TonyNeural",             # Energetic
    "calm": "en-US-JasonNeural",                 # Calm / soft
    "british": "en-GB-RyanNeural",               # British documentary
    "australian": "en-AU-WilliamNeural",         # Australian
}

DEFAULT_VOICE = "en-US-GuyNeural"


def _edge_tts_available() -> bool:
    return subprocess.run(["edge-tts", "--help"], capture_output=True).returncode == 0


def generate_voiceover(text: str, out_path: str, voice: Optional[str] = None) -> str:
    """Generate voiceover using the best available backend.

    Tries Edge TTS first (free, natural), falls back to pyttsx3.
    """
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    # Try Edge TTS
    if _edge_tts_available():
        return _generate_edge_tts(text, out_path, voice)

    # Fallback to pyttsx3
    logger.warning("Edge TTS not found; falling back to pyttsx3 (robotic). "
                   "Install Edge TTS for natural voice: pip install edge-tts")
    return _generate_pyttsx3(text, out_path)


def _generate_edge_tts(text: str, out_path: str, voice: Optional[str] = None) -> str:
    """Use Microsoft Edge TTS (free, high quality, no API key)."""
    v = voice or DEFAULT_VOICE
    # Split long text into chunks if needed (edge-tts has CLI limits)
    max_chars = 3000
    if len(text) > max_chars:
        text = text[:max_chars]

    cmd = [
        "edge-tts",
        "--voice", v,
        "--text", text,
        "--write-media", out_path,
    ]
    logger.info("[TTS] Edge TTS: voice=%s", v)
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _generate_pyttsx3(text: str, out_path: str) -> str:
    """Offline fallback using pyttsx3."""
    import pyttsx3  # type: ignore
    engine = pyttsx3.init()
    engine.setProperty("rate", 165)
    engine.setProperty("volume", 1.0)
    voices = engine.getProperty("voices")
    for v in voices:
        if "male" in v.name.lower() or "guy" in v.name.lower():
            engine.setProperty("voice", v.id)
            break
    engine.save_to_file(text, out_path)
    engine.runAndWait()
    return out_path


def generate_high_quality_voiceover(text: str, out_path: str, elevenlabs_key: str) -> str:
    """Generate voiceover using ElevenLabs API (best quality, paid)."""
    import requests
    url = "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
    headers = {"xi-api-key": elevenlabs_key, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }
    r = requests.post(url, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(r.content)
    return out_path


def generate_emotional_voiceover(text: str, out_path: str, emotion: str = "dramatic") -> str:
    """Pick an Edge TTS voice based on emotion."""
    voice_map = {
        "emotional": EDGE_VOICES["emotional"],
        "inspiring": EDGE_VOICES["energetic"],
        "dramatic": EDGE_VOICES["deep_documentary"],
        "mysterious": EDGE_VOICES["calm"],
        "funny": EDGE_VOICES["energetic"],
        "shocking": EDGE_VOICES["deep_documentary"],
        "intense": EDGE_VOICES["deep_documentary"],
    }
    voice = voice_map.get(emotion, DEFAULT_VOICE)
    return generate_voiceover(text, out_path, voice=voice)
