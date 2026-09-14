"""Fetch royalty-free music tracks matching emotion/mood.

Sources:
- Freesound.org (requires API key, best quality)
- Local music_library/ folder (fallback, no key needed)

All tracks should be royalty-free / Creative Commons to avoid copyright strikes.
"""
import logging
import os
import random
import shutil
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# Emotion → search keywords for royalty-free music
EMOTION_KEYWORDS = {
    "emotional": ["emotional cinematic", "sad piano", "emotional orchestral"],
    "inspiring": ["epic inspiring", "uplifting orchestral", "motivational"],
    "nostalgic": ["lofi chill", "nostalgic ambient", "retro synth"],
    "dramatic": ["dark trap beat", "dramatic intense", "cinematic tension"],
    "mysterious": ["mystery ambient", "dark phonk", "cinematic phonk"],
    "funny": ["comedy upbeat", "funny meme music", "quirky"],
    "shocking": ["hard phonk", "aggressive trap", "intense bass"],
    "intense": ["dark trap beat", "phonk drift", "intense phonk"],
}

# Local library structure: music_library/{emotion}/track.mp3
LOCAL_LIBRARY = "music_library"


def _ensure_local_library():
    """Create local music library folders."""
    for emotion in EMOTION_KEYWORDS:
        os.makedirs(os.path.join(LOCAL_LIBRARY, emotion), exist_ok=True)


def search_freesound(query: str, api_key: Optional[str] = None, max_results: int = 5) -> List[Dict]:
    """Search Freesound.org for royalty-free music."""
    if not api_key:
        return []
    try:
        url = "https://freesound.org/apiv2/search/text/"
        params = {
            "query": query,
            "token": api_key,
            "filter": "duration:[10.0 TO 120.0]",
            "sort": "downloads_desc",
            "fields": "id,name,previews,duration,license,tags",
            "page_size": max_results,
        }
        r = requests.get(url, params=params, timeout=15)
        if not r.ok:
            return []
        data = r.json()
        results = []
        for item in data.get("results", []):
            previews = item.get("previews", {})
            results.append({
                "id": item["id"],
                "name": item["name"],
                "preview_url": previews.get("preview-hq-mp3"),
                "duration": item.get("duration"),
                "license": item.get("license"),
                "tags": item.get("tags", []),
                "source": "freesound",
            })
        return results
    except Exception as e:
        logger.warning("Freesound search failed: %s", e)
    return []


def download_freesound_preview(track: Dict, out_path: str) -> bool:
    """Download a Freesound preview MP3."""
    url = track.get("preview_url")
    if not url:
        return False
    try:
        r = requests.get(url, timeout=20)
        if r.ok:
            with open(out_path, "wb") as f:
                f.write(r.content)
            return True
    except Exception as e:
        logger.warning("Freesound download failed: %s", e)
    return False


def get_local_tracks(emotion: str) -> List[str]:
    """Return paths to locally stored tracks for an emotion."""
    _ensure_local_library()
    folder = os.path.join(LOCAL_LIBRARY, emotion)
    if not os.path.exists(folder):
        return []
    tracks = []
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith((".mp3", ".wav", ".ogg", ".m4a")):
            tracks.append(os.path.join(folder, f))
    return tracks


def get_track_for_emotion(
    emotion: str,
    freesound_key: Optional[str] = None,
    out_dir: str = "output",
    prefer_local: bool = True,
) -> Optional[str]:
    """Get a royalty-free music track matching the emotion.

    Strategy:
    1. Check local library first (if prefer_local=True)
    2. Search Freesound (if API key provided)
    3. Return None if nothing found

    Returns path to downloaded track or local track.
    """
    logger.info("[MUSIC] Getting track for emotion: %s", emotion)

    # 1. Local library
    if prefer_local:
        local = get_local_tracks(emotion)
        if local:
            track = random.choice(local)
            logger.info("[MUSIC] Using local track: %s", track)
            return track

    # 2. Freesound
    if freesound_key:
        keywords = EMOTION_KEYWORDS.get(emotion, ["cinematic background"])
        for kw in keywords:
            results = search_freesound(kw, api_key=freesound_key, max_results=5)
            if results:
                # Pick the longest track (better for editing)
                best = max(results, key=lambda x: x.get("duration", 0))
                out_path = os.path.join(out_dir, f"music_{emotion}_{best['id']}.mp3")
                if os.path.exists(out_path):
                    return out_path
                if download_freesound_preview(best, out_path):
                    logger.info("[MUSIC] Downloaded from Freesound: %s", best["name"])
                    return out_path

    logger.warning("[MUSIC] No track found for emotion: %s", emotion)
    return None


def list_local_library() -> Dict[str, List[str]]:
    """List all tracks in the local music library."""
    _ensure_local_library()
    out = {}
    for emotion in EMOTION_KEYWORDS:
        tracks = get_local_tracks(emotion)
        out[emotion] = [os.path.basename(t) for t in tracks]
    return out
