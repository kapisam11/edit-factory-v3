"""AI Video Factory — Capability Registry.

Decouples the pipeline from hard API dependencies.
Each capability (TTS, music, research, etc.) can have multiple providers
with priority-based fallback.
"""
import os
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Optional runtime backends: explicitly typed as Any so strict mypy doesn't
# fail on missing third-party stubs when the package is not installed.
elevenlabs: Any = None
pyttsx3: Any = None
Groq: Any = None


@dataclass
class ProviderResult:
    success: bool
    data: Any
    provider_name: str
    error: Optional[str] = None


class Provider(ABC):
    """Base class for all capability providers."""
    name: str = "base"
    priority: int = 0  # Higher = tried first
    requires_key: bool = False
    key_env_var: Optional[str] = None

    def is_available(self) -> bool:
        """Check if this provider can be used right now."""
        if self.requires_key and self.key_env_var:
            return bool(os.environ.get(self.key_env_var))
        return True

    @abstractmethod
    def call(self, *args: Any, **kwargs: Any) -> ProviderResult:
        ...


class CapabilityRegistry:
    """Central registry for all external capabilities."""

    def __init__(self) -> None:
        self._providers: Dict[str, List[Provider]] = {}

    def register(self, capability: str, provider: Provider) -> None:
        """Register a provider for a capability."""
        if capability not in self._providers:
            self._providers[capability] = []
        self._providers[capability].append(provider)
        self._providers[capability].sort(key=lambda p: p.priority, reverse=True)

    def get(self, capability: str) -> Optional[Provider]:
        """Get the highest-priority available provider."""
        for provider in self._providers.get(capability, []):
            if provider.is_available():
                return provider
        return None

    def call(self, capability: str, *args: Any, **kwargs: Any) -> ProviderResult:
        """Call the best available provider, with fallback chain."""
        providers = self._providers.get(capability, [])
        if not providers:
            return ProviderResult(
                success=False,
                data=None,
                provider_name="none",
                error=f"No providers registered for capability: {capability}",
            )

        last_error: Optional[str] = None
        for provider in providers:
            if not provider.is_available():
                continue
            try:
                return provider.call(*args, **kwargs)
            except Exception as e:
                last_error = f"{provider.name}: {e}"
                continue

        return ProviderResult(
            success=False,
            data=None,
            provider_name="all",
            error=f"All providers failed. Last: {last_error}",
        )

    def list_capabilities(self) -> Dict[str, List[str]]:
        """List all registered capabilities and their providers."""
        return {
            cap: [p.name for p in providers]
            for cap, providers in self._providers.items()
        }

    def list_available(self) -> Dict[str, str]:
        """List which provider is currently active for each capability."""
        result: Dict[str, str] = {}
        for cap in self._providers:
            provider = self.get(cap)
            result[cap] = provider.name if provider else "none"
        return result


# --- Concrete Provider Examples ---

class ElevenLabsTTSProvider(Provider):
    """High-quality paid TTS."""
    name = "elevenlabs"
    priority = 100
    requires_key = True
    key_env_var = "ELEVENLABS_API_KEY"

    def call(self, text: str, output_path: str, **kwargs: Any) -> ProviderResult:
        try:
            if elevenlabs is None:
                raise ImportError("elevenlabs not installed")
            from elevenlabs import generate, save  # type: ignore[import-not-found]
            audio = generate(text=text, voice="Bella", model="eleven_monolingual_v1")
            save(audio, output_path)
            return ProviderResult(success=True, data=output_path, provider_name=self.name)
        except Exception as e:
            return ProviderResult(success=False, data=None, provider_name=self.name, error=str(e))


class EdgeTTSProvider(Provider):
    """Free, natural-sounding TTS via Microsoft Edge."""
    name = "edge_tts"
    priority = 50
    requires_key = False

    def call(self, text: str, output_path: str, voice: str = "en-US-GuyNeural", **kwargs: Any) -> ProviderResult:
        try:
            import asyncio
            import edge_tts

            async def _generate() -> None:
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(output_path)

            asyncio.run(_generate())
            return ProviderResult(success=True, data=output_path, provider_name=self.name)
        except Exception as e:
            return ProviderResult(success=False, data=None, provider_name=self.name, error=str(e))


class Pyttsx3TTSProvider(Provider):
    """Offline fallback TTS."""
    name = "pyttsx3"
    priority = 10
    requires_key = False

    def call(self, text: str, output_path: str, **kwargs: Any) -> ProviderResult:
        try:
            if pyttsx3 is None:
                raise ImportError("pyttsx3 not installed")
            engine = pyttsx3.init()
            engine.save_to_file(text, output_path)
            engine.runAndWait()
            return ProviderResult(success=True, data=output_path, provider_name=self.name)
        except Exception as e:
            return ProviderResult(success=False, data=None, provider_name=self.name, error=str(e))


class GroqResearchProvider(Provider):
    """Groq API for research enrichment."""
    name = "groq"
    priority = 100
    requires_key = True
    key_env_var = "GROQ_API_KEY"

    def call(self, query: str, **kwargs: Any) -> ProviderResult:
        try:
            if Groq is None:
                raise ImportError("groq not installed")
            client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
            response = client.chat.completions.create(
                model="llama3-8b-8192",
                messages=[{"role": "user", "content": query}],
            )
            return ProviderResult(
                success=True,
                data=response.choices[0].message.content,
                provider_name=self.name,
            )
        except Exception as e:
            return ProviderResult(success=False, data=None, provider_name=self.name, error=str(e))


class LocalResearchProvider(Provider):
    """Fallback: uses DuckDuckGo search + scraping."""
    name = "local_research"
    priority = 50
    requires_key = False

    def call(self, query: str, **kwargs: Any) -> ProviderResult:
        try:
            from .web_browser import deep_research
            result = deep_research(query, max_search=5, max_scrape=3)
            return ProviderResult(success=True, data=result, provider_name=self.name)
        except Exception as e:
            return ProviderResult(success=False, data=None, provider_name=self.name, error=str(e))


class FreesoundMusicProvider(Provider):
    """Fetch royalty-free music from Freesound."""
    name = "freesound"
    priority = 100
    requires_key = True
    key_env_var = "FREESOUND_API_KEY"

    def call(self, emotion: str, output_path: str, **kwargs: Any) -> ProviderResult:
        try:
            from .music_fetcher import get_track_for_emotion
            track = get_track_for_emotion(emotion, freesound_key=os.environ.get("FREESOUND_API_KEY"))
            return ProviderResult(success=True, data=track, provider_name=self.name)
        except Exception as e:
            return ProviderResult(success=False, data=None, provider_name=self.name, error=str(e))


class LocalMusicProvider(Provider):
    """Use local music library."""
    name = "local_music"
    priority = 50
    requires_key = False

    def call(self, emotion: str, output_path: str, **kwargs: Any) -> ProviderResult:
        import glob
        import shutil
        library_dir = f"music_library/{emotion}"
        if not os.path.exists(library_dir):
            return ProviderResult(
                success=False, data=None, provider_name=self.name,
                error=f"No local music library for emotion: {emotion}",
            )
        tracks = glob.glob(os.path.join(library_dir, "*.mp3"))
        if not tracks:
            return ProviderResult(
                success=False, data=None, provider_name=self.name,
                error=f"No tracks in {library_dir}",
            )
        selected = random.choice(tracks)
        shutil.copy(selected, output_path)
        return ProviderResult(success=True, data=output_path, provider_name=self.name)


def build_default_registry() -> CapabilityRegistry:
    """Build a registry with all default providers registered."""
    reg = CapabilityRegistry()
    reg.register("tts", ElevenLabsTTSProvider())
    reg.register("tts", EdgeTTSProvider())
    reg.register("tts", Pyttsx3TTSProvider())
    reg.register("research", GroqResearchProvider())
    reg.register("research", LocalResearchProvider())
    reg.register("music", FreesoundMusicProvider())
    reg.register("music", LocalMusicProvider())
    return reg
