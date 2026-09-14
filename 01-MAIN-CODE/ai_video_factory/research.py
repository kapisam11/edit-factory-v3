"""Research utilities for AI Video Factory.

This module performs enhanced topic research used to shape the edit. It
provides a compact programmatic summary (who/what, why care, conflict,
emotion, strongest angle) plus a small set of supporting visuals and a
basic "trending" signal. The functions are defensive and work offline
with best-effort web lookups when `requests` is available.

The module also contains a lightweight hook for calling Groq (or other
LLM/trend providers). The Groq call is optional and wrapped so failures
do not break the workflow.
"""
from typing import Dict, List, Optional
import time
import re
from .visuals_fetcher import fetch_visuals


def _safe_get_json(url: str, params=None, timeout=6):
    try:
        import requests

        r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "ai-video-factory/1.0"})
        if r.ok:
            return r.json()
    except Exception:
        return None


def _fetch_wikipedia_summary(topic: str) -> Optional[Dict[str, str]]:
    data = _safe_get_json(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "prop": "extracts|info", "exintro": 1, "titles": topic, "format": "json", "inprop": "url"},
    )
    if not data:
        return None
    try:
        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return None
        page = next(iter(pages.values()))
        return {"extract": page.get("extract", "")[:1000], "url": page.get("fullurl")}
    except Exception:
        return None


def _duckduckgo_search(topic: str, limit: int = 6) -> List[Dict[str, str]]:
    """Scrape DuckDuckGo HTML search results for quick links and snippets.

    This is a lightweight fallback that does not require API keys.
    """
    try:
        import requests

        q = topic
        r = requests.get("https://duckduckgo.com/html/", params={"q": q}, timeout=6, headers={"User-Agent": "ai-video-factory/1.0"})
        if not r.ok:
            return []
        html = r.text
        # Rough but effective regex-based scraping for result titles/links
        items = []
        for m in re.finditer(r'<a rel="nofollow" class="result__a" href="(.*?)">(.*?)</a>', html, flags=re.S):
            href = m.group(1)
            title = re.sub(r'<.*?>', '', m.group(2)).strip()
            items.append({"title": title, "url": href})
            if len(items) >= limit:
                break
        return items
    except Exception:
        return []


def _detect_recent_news(results: List[Dict[str, str]]) -> bool:
    """Heuristic: treat presence of news-like domains or 'news' in URL as trending."""
    if not results:
        return False
    news_indicators = ("news", "reddit", "youtube.com", "twitter.com", "x.com")
    for r in results:
        u = r.get("url", "").lower()
        if any(ind in u for ind in news_indicators):
            return True
    return False


def research_topic(topic: str, use_groq: bool = False, groq_api_key: Optional[str] = None) -> Dict[str, object]:
    """Return an enhanced research summary for `topic`.

    Returns a dict with the following keys:
      - topic
      - who_what
      - why_care
      - main_conflict
      - emotion
      - strongest_angle
      - sources (list)
      - visuals (list of image urls / suggested screenshots)
      - trending (bool)

    If `use_groq` is True and `groq_api_key` is provided the function will
    attempt an optional Groq-assisted enrichment. Failures are ignored.
    """
    summary = {
        "topic": topic,
        "who_what": "",
        "why_care": "",
        "main_conflict": "",
        "emotion": "dramatic",
        "strongest_angle": "",
        "content_type": _classify_content_type(topic),
        "platform_focus": "Shorts / Reels / TikTok",
        "sources": [],
        "visuals": [],
        "trending": False,
    }

    # 1) Wikipedia extract
    wiki = _fetch_wikipedia_summary(topic)
    if wiki:
        summary.update({
            "who_what": wiki.get("extract", "")[:800],
            "why_care": f"Because {topic} has notable events, history and community interest.",
            "main_conflict": "Identify a surprising choice, betrayal, or turning point.",
            "strongest_angle": f"Focus on human choices and consequences around {topic}.",
        })
        summary["sources"].append(wiki.get("url"))
    else:
        summary.update({
            "who_what": f"A concise intro to {topic}.",
            "why_care": "Community interest and emotional stakes.",
            "main_conflict": "A clear conflict or surprising reveal to drive retention.",
            "strongest_angle": "Human choices, consequences, and mystery.",
        })

    # 2) Search results for supporting visuals & trending signal
    results = _duckduckgo_search(topic, limit=8)
    for r in results:
        url = r.get("url")
        if url:
            summary["sources"].append(url)
    summary["trending"] = _detect_recent_news(results)

    # 3) Derive simple visuals by extracting OpenGraph images (best-effort)
    # Use the new visuals_fetcher to collect scored, purpose-tagged visuals
    try:
        vis = fetch_visuals(topic, max_items=12)
        # store structured visuals (dicts)
        if vis:
            summary["visuals"] = vis
    except Exception:
        # fallback: extract OpenGraph images (best-effort)
        try:
            import requests

            for r in results[:6]:
                u = r.get("url")
                if not u:
                    continue
                try:
                    rr = requests.get(u, timeout=5, headers={"User-Agent": "ai-video-factory/1.0"})
                    if rr.ok:
                        m = re.search(r'<meta property="og:image" content="([^\"]+)"', rr.text)
                        if m:
                            summary["visuals"].append({"url": m.group(1), "source": u, "purpose": "context", "score": 0.5})
                except Exception:
                    continue
        except Exception:
            pass

    # 4) Optional Groq enrichment hook (best-effort; not required to run)
    if use_groq and groq_api_key:
        try:
            _groq_enrich(topic, summary, groq_api_key)
        except Exception:
            # ignore Groq failures; the rest of the summary is usable
            pass

    # ensure uniqueness and truncate lists
    summary["sources"] = list(dict.fromkeys([s for s in summary["sources"] if s]))[:12]
    # visuals may be dicts; dedupe by URL
    seen_urls = set()
    unique_visuals = []
    for v in summary.get("visuals", []):
        try:
            url = v.get("url") if isinstance(v, dict) else str(v)
        except Exception:
            url = str(v)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique_visuals.append(v)
        if len(unique_visuals) >= 8:
            break
    summary["visuals"] = unique_visuals

    return summary


def _groq_enrich(topic: str, summary: Dict[str, object], api_key: str):
    """Optional helper to enrich `summary` via a Groq call.

    This function is intentionally conservative: it performs a small
    POST request and attempts to merge back a short improved angle or
    supporting facts. If Groq's API shape changes, this helper should be
    adapted. Keep your API key in the environment or pass it in at runtime.
    """
    try:
        import requests

        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "llama3-8b-8192",
            "messages": [
                {"role": "system", "content": "You are a viral video research assistant."},
                {"role": "user", "content": f"Research signals for topic: {topic}. Provide 3 short facts and one strong viral angle (1-2 sentences)."}
            ],
            "max_tokens": 256,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        r = requests.post(url, json=payload, headers=headers, timeout=12)
        if not r.ok:
            return
        data = r.json()
        output = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        text = str(output) if output else ""
        if not text:
            return

        summary["groq_excerpt"] = text[:1200]
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            low = line.lower()
            if low.startswith("hook:"):
                summary["strongest_angle"] = line.split(":", 1)[1].strip()
            elif low.startswith("title:"):
                summary["viral_title"] = line.split(":", 1)[1].strip()
            elif low.startswith("emotion:"):
                summary["emotion"] = line.split(":", 1)[1].strip().lower()
            elif low.startswith("angle:"):
                summary["strongest_angle"] = line.split(":", 1)[1].strip()
            elif low.startswith("facts:"):
                summary["research_facts"] = line.split(":", 1)[1].strip()
            elif low.startswith("subtitle:"):
                summary["subtitle_hint"] = line.split(":", 1)[1].strip()
    except Exception:
        return


def _classify_content_type(topic: str) -> str:
    normalized = topic.lower()
    if any(k in normalized for k in ("minecraft", "smp", "bedrock", "java")):
        return "Minecraft"
    if any(k in normalized for k in ("gameplay", "gaming", "war", "battle", "esports", "ranked")):
        return "Gaming"
    if any(k in normalized for k in ("documentary", "history", "mystery", "true story", "real life")):
        return "Documentary"
    if any(k in normalized for k in ("betray", "betrayal", "friendship", "rival", "legend", "mystic")):
        return "Drama"
    return "Story"
