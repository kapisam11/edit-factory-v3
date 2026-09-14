"""Fetch and score supporting visuals for a topic.

Provides Wikimedia Commons image search, Reddit image discovery, and
optional YouTube discovery via `yt-dlp` if available. Returns a list of
visual candidate dicts with metadata and a suggested `purpose` tag.
"""
from typing import List, Dict
import requests
import re
import shutil
import subprocess
import json

from .render_engine import run_ffmpeg


def _search_wikimedia(topic: str, limit: int = 6) -> List[Dict]:
    """Search Wikimedia Commons for images matching topic."""
    results = []
    try:
        url = "https://commons.wikimedia.org/w/api.php"
        params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": topic, "gsrlimit": limit, "prop": "imageinfo|info", "iiprop": "url|extmetadata"}
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": "ai-video-factory/1.0"})
        if not r.ok:
            return results
        j = r.json()
        pages = j.get("query", {}).get("pages", {})
        for p in pages.values():
            if "imageinfo" in p:
                ii = p["imageinfo"][0]
                src = ii.get("url")
                meta = ii.get("extmetadata", {})
                license = meta.get("LicenseShortName", {}).get("value") if meta else None
                title = p.get("title")
                artist = None
                license_url = None
                if meta:
                    artist = meta.get("Artist", {}).get("value") or meta.get("Credit", {}).get("value")
                    license_url = meta.get("LicenseUrl", {}).get("value")
                results.append({"url": src, "source": "wikimedia", "title": title, "license": license, "artist": artist, "license_url": license_url})
    except Exception:
        return results
    return results


def _search_reddit(topic: str, limit: int = 8) -> List[Dict]:
    """Use Reddit's public search JSON to find image posts related to topic."""
    results = []
    try:
        url = "https://www.reddit.com/search.json"
        params = {"q": topic, "limit": limit, "sort": "relevance"}
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": "ai-video-factory/1.0"})
        if not r.ok:
            return results
        j = r.json()
        for item in j.get("data", {}).get("children", []):
            d = item.get("data", {})
            # prefer preview images or url if it ends with image ext
            url_candidate = None
            if d.get("preview"):
                images = d["preview"].get("images", [])
                if images:
                    url_candidate = images[0].get("source", {}).get("url")
            if not url_candidate:
                u = d.get("url_overridden_by_dest") or d.get("url")
                if u and re.search(r"\.(jpg|jpeg|png|gif)$", u, flags=re.I):
                    url_candidate = u
            if url_candidate:
                results.append({"url": url_candidate, "source": "reddit", "title": d.get("title"), "license": "reddit", "subreddit": d.get("subreddit"), "selftext": (d.get("selftext") or "")[:400]})
    except Exception:
        return results
    return results


def _search_youtube(topic: str, limit: int = 6) -> List[Dict]:
    """Use `yt-dlp` with `ytsearch` to find YouTube videos; requires `yt-dlp` on PATH."""
    results = []
    if not shutil.which("yt-dlp"):
        return results
    try:
        query = f"ytsearch{limit}:{topic}"
        proc = subprocess.run(["yt-dlp", "--dump-single-json", query], capture_output=True, text=True, timeout=30)
        if proc.returncode != 0 or not proc.stdout:
            return results
        data = json.loads(proc.stdout)
        entries = data.get("entries") or [data]
        for e in entries[:limit]:
            vid = e.get("id")
            title = e.get("title")
            thumb = e.get("thumbnail")
            url = f"https://www.youtube.com/watch?v={vid}"
            results.append({"url": url, "source": "youtube", "title": title, "thumbnail": thumb})
    except Exception:
        return results
    return results


def _search_youtube_fallback(topic: str, limit: int = 6) -> List[Dict]:
    """Fallback YouTube search by scraping the search results page. Returns video URLs and thumbnails."""
    results = []
    try:
        q = requests.utils.requote_uri(topic)
        url = f"https://www.youtube.com/results?search_query={q}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "ai-video-factory/1.0"})
        if not r.ok:
            return results
        text = r.text
        # find video ids
        ids = re.findall(r"/watch\?v=([A-Za-z0-9_-]{11})", text)
        seen = []
        for vid in ids:
            if vid in seen:
                continue
            seen.append(vid)
            vurl = f"https://www.youtube.com/watch?v={vid}"
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
            results.append({"url": vurl, "source": "youtube", "title": "", "thumbnail": thumb})
            if len(results) >= limit:
                break
    except Exception:
        return results
    return results


def _score_and_tag(item: Dict, topic: str) -> Dict:
    """Assign a simple relevance score and a purpose tag based on heuristics."""
    url = item.get("url", "")
    title = (item.get("title") or "") .lower()
    score = 0.5
    purpose = "context"
    # additional granularity
    if any(k in title for k in ("clip", "gameplay", "game", "smp", "minecraft")):
        purpose = "gameplay_clip"
    # heuristics
    if any(k in title for k in ("betray", "betrayal", "final", "secret", "reveal", "twist", "climax", "kill", "win", "fail")):
        score += 0.3
        purpose = "conflict"
    if any(k in title for k in ("meme", "funny", "lol", "wtf")):
        score += 0.2
        purpose = "meme"
    if item.get("source") == "wikimedia":
        score += 0.1
        purpose = "context"
    if item.get("source") == "youtube":
        score += 0.2
        purpose = purpose if purpose != "context" else "clip"
    item.update({"score": round(min(1.0, score), 2), "purpose": purpose})
    return item


def annotate_purposes(visuals: List[Dict], summary: Dict) -> List[Dict]:
    """Assign purpose tags for visuals relative to the research `summary`.

    Uses keywords from `who_what`, `main_conflict`, and `strongest_angle` to match.
    Returns a new list with updated `purpose` and `match_score` fields.
    """
    if not visuals:
        return visuals
    # build keyword set from summary
    txt = " ".join([str(summary.get(k, "")) for k in ("who_what", "main_conflict", "strongest_angle")])
    keywords = set(re.findall(r"[A-Za-z]{3,}", txt.lower()))
    beat_keywords = {
        "hook": {"surprise", "shocking", "secret", "never", "first", "didn\'t"},
        "conflict": {"betray", "attack", "fight", "steal", "exposed", "ruin", "kicked", "ban"},
        "payoff": {"win", "victory", "revealed", "paid off", "success", "revenge"},
        "reaction": {"shocked", "funny", "laugh", "wow", "amazed"},
    }

    out = []
    for v in visuals:
        text_fields = " ".join([str(v.get(k, "")) for k in ("title", "subreddit", "selftext", "description", "url")]).lower()
        match_score = 0
        purpose_hits = {}
        for beat, kws in beat_keywords.items():
            hits = sum(1 for kw in kws if kw in text_fields or kw in keywords)
            if hits:
                purpose_hits[beat] = hits
                match_score += hits
        # choose strongest beat
        if purpose_hits:
            chosen = max(purpose_hits.keys(), key=lambda b: purpose_hits[b])
            # map beat to purpose categories
            if chosen == "hook":
                v["purpose"] = "hook"
            elif chosen == "conflict":
                v["purpose"] = "conflict"
            elif chosen == "payoff":
                v["purpose"] = "payoff"
            elif chosen == "reaction":
                v["purpose"] = "reaction"
        # refine with existing heuristics
        v = _score_and_tag(v, summary.get("topic", ""))
        v["match_score"] = min(1.0, 0.1 * match_score + v.get("score", 0))
        out.append(v)
    return out


def vet_with_model(visuals: List[Dict], summary: Dict, api_key: str) -> List[Dict]:
    """Ask an optional model to re-classify visuals by purpose.

    The model is asked to return JSON array of objects: {"url":..., "purpose":..., "confidence":0-1}
    If the model call fails or returns invalid JSON, falls back to `annotate_purposes`.
    """
    try:
        from .model_adapter import call_model
        import json

        prompt = (
            "You are given a research summary and a list of candidate visuals (url, title, thumbnail, source). "
            "Return a JSON array where each item has keys: url, purpose (one of hook, conflict, payoff, reaction, meme, context, gameplay_clip), confidence (0-1). "
            "Do not include extra text. Research summary:\n" + json.dumps(summary) + "\nVisuals:\n" + json.dumps(visuals) + "\n"
        )
        resp = call_model(prompt, api_key)
        if not resp:
            return annotate_purposes(visuals, summary)
        # try to extract JSON from response
        jb = None
        try:
            jb = json.loads(resp)
        except Exception:
            # try find first JSON block
            m = re.search(r"(\[\s*\{[\s\S]*\}\s*\])", resp)
            if m:
                try:
                    jb = json.loads(m.group(1))
                except Exception:
                    jb = None
        if not jb or not isinstance(jb, list):
            return annotate_purposes(visuals, summary)
        # merge model outputs into visuals
        url_to_v = {v.get("url"): v for v in visuals if v.get("url")}
        out = []
        for item in jb:
            url = item.get("url")
            if not url:
                continue
            base = url_to_v.get(url, {})
            merged = base.copy()
            merged["purpose"] = item.get("purpose") or merged.get("purpose")
            merged["model_confidence"] = float(item.get("confidence") or 0)
            out.append(merged)
        # append any visuals not returned by model using heuristics
        for v in visuals:
            if v.get("url") not in {o.get("url") for o in out}:
                out.append(v)
        return out
    except Exception:
        return annotate_purposes(visuals, summary)


def fetch_visuals(topic: str, max_items: int = 12) -> List[Dict]:
    """Return a scored list of visual candidate dicts for `topic`.

    Each dict contains: url, source, title, license (optional), score, purpose
    """
    items = []
    # Wikimedia
    items.extend(_search_wikimedia(topic, limit=6))
    # Reddit
    items.extend(_search_reddit(topic, limit=8))
    # YouTube (thumbnails / video links)
    yt_items = _search_youtube(topic, limit=6)
    if not yt_items:
        yt_items = _search_youtube_fallback(topic, limit=6)
    items.extend(yt_items)

    # score/tag
    scored = []
    for it in items:
        scored.append(_score_and_tag(it, topic))

    # sort by score desc and unique by url
    seen = set()
    out = []
    for s in sorted(scored, key=lambda x: x.get("score", 0), reverse=True):
        u = s.get("url")
        if not u or u in seen:
            continue
        out.append(s)
        seen.add(u)
        if len(out) >= max_items:
            break

    return out


def _is_meme(item: Dict) -> bool:
    """Lightweight meme/context detection using title/source/subreddit heuristics."""
    title = (item.get("title") or "").lower()
    src = (item.get("source") or "").lower()
    if "meme" in title or "dank" in title or "starterpack" in title:
        return True
    if src == "reddit" and any(k in (item.get("subreddit") or "").lower() for k in ("memes", "dankmemes", "wholesomememes")):
        return True
    if any(pat in title for pat in ("when ", "that moment", "mfw", "me when", "pov", "starter pack", "expectation vs reality")):
        return True
    return False


def download_youtube_clip(youtube_url: str, out_dir: str, max_duration: int = 20) -> Dict:
    """Download a short clip from a YouTube URL using `yt-dlp` + `ffmpeg`.

    Returns metadata dict with `local_path`, `thumbnail`, `uploader`, `upload_date`, `source`.
    Requires `yt-dlp` and `ffmpeg` on PATH. If not available, returns empty dict.
    """
    import os
    import shlex
    from datetime import datetime

    if not shutil.which("yt-dlp"):
        return {}
    os.makedirs(out_dir, exist_ok=True)
    try:
        proc = subprocess.run(["yt-dlp", "--dump-single-json", youtube_url], capture_output=True, text=True, timeout=40)
        if proc.returncode != 0 or not proc.stdout:
            return {}
        info = json.loads(proc.stdout)
        video_id = info.get("id")
        title = info.get("title")
        uploader = info.get("uploader")
        upload_date = info.get("upload_date")
        tmp_name = os.path.join(out_dir, f"{video_id}.%(ext)s")
        dl = subprocess.run(["yt-dlp", "-f", "bestvideo[ext=mp4]+bestaudio/best", "-o", tmp_name, youtube_url], capture_output=True, text=True, timeout=300)
        if dl.returncode != 0:
            return {}
        downloaded = None
        for f in os.listdir(out_dir):
            if f.startswith(video_id + "."):
                downloaded = os.path.join(out_dir, f)
                break
        if not downloaded:
            for f in os.listdir(out_dir):
                if video_id in f:
                    downloaded = os.path.join(out_dir, f)
                    break
        if not downloaded:
            return {}
        out_clip = os.path.join(out_dir, f"{video_id}_clip_{max_duration}s.mp4")
        if shutil.which("ffmpeg"):
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                downloaded,
                "-ss",
                "0",
                "-t",
                str(int(max_duration)),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                out_clip,
            ]
            try:
                run_ffmpeg(cmd, timeout=120)
            except (OSError, RuntimeError):
                out_clip = downloaded
        else:
            out_clip = downloaded
        thumb = None
        if shutil.which("ffmpeg"):
            thumb_path = os.path.join(out_dir, f"{video_id}_thumb.jpg")
            cmd = ["ffmpeg", "-y", "-i", out_clip, "-ss", "00:00:01", "-vframes", "1", thumb_path]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=20)
                if os.path.exists(thumb_path):
                    thumb = thumb_path
            except Exception:
                thumb = None
        meta = {
            "local_path": out_clip,
            "thumbnail": thumb,
            "source": "youtube",
            "title": title,
            "uploader": uploader,
            "upload_date": upload_date,
            "url": youtube_url,
        }
        return meta
    except Exception:
        return {}


def download_visuals(visuals: List[Dict], out_dir: str, download_clips: bool = True, clip_max_duration: int = 20) -> List[Dict]:
    """Download visuals (images and optional youtube clips) into `out_dir`.

    Returns list of metadata dicts with `local_path` and provenance fields.
    """
    import os
    from datetime import datetime

    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for i, v in enumerate(visuals):
        try:
            if isinstance(v, dict):
                url = v.get("url")
            else:
                url = v
                v = {"url": url}
            if not url:
                continue
            if "youtube.com" in url or "youtu.be" in url:
                if download_clips:
                    meta = download_youtube_clip(url, out_dir, max_duration=clip_max_duration)
                    if meta:
                        meta.update(v)
                        meta["downloaded_at"] = datetime.utcnow().isoformat() + "Z"
                        saved.append(meta)
                        continue
                if v.get("thumbnail"):
                    try:
                        r = requests.get(v["thumbnail"], timeout=12, headers={"User-Agent": "ai-video-factory/1.0"})
                        if r.ok:
                            fn = os.path.join(out_dir, f"thumb_{i+1}.jpg")
                            with open(fn, "wb") as wf:
                                wf.write(r.content)
                            v["local_thumbnail"] = fn
                    except Exception:
                        pass
                v["source"] = v.get("source", "youtube")
                saved.append(v)
                continue
            try:
                r = requests.get(url, timeout=12, headers={"User-Agent": "ai-video-factory/1.0"})
                if r.ok and r.content:
                    ext = os.path.splitext(url.split("?")[0])[1]
                    if not ext:
                        ext = ".jpg"
                    fname = f"img_{i+1}{ext}"
                    fn = os.path.join(out_dir, fname)
                    with open(fn, "wb") as wf:
                        wf.write(r.content)
                    v["local_path"] = fn
                    v["downloaded_at"] = datetime.utcnow().isoformat() + "Z"
                    if _is_meme(v):
                        v["purpose"] = "meme"
                    saved.append(v)
            except Exception:
                continue
        except Exception:
            continue
    return saved


def prune_visuals(visuals: List[Dict], min_match_score: float = 0.25, min_motion: float = 0.07, require_faces_for: List[str] = None, min_model_confidence: float = 0.0, motion_thresholds: Dict[str, float] = None) -> (List[Dict], List[Dict]):
    """Prune visuals by thresholds.

    Returns (kept, removed) lists. `require_faces_for` is list of purpose tags
    (e.g., ["hook", "payoff"]) that require faces_detected>0 to keep.
    """
    if require_faces_for is None:
        require_faces_for = ["hook", "payoff", "conflict"]
    kept = []
    removed = []
    for v in visuals:
        reason = None
        if v.get("match_score", 0) < min_match_score:
            reason = f"low_match_score({v.get('match_score')})"
        # choose motion threshold by purpose if specified
        purpose = v.get("purpose") or ""
        chosen_motion_threshold = min_motion
        if motion_thresholds and purpose in motion_thresholds:
            try:
                chosen_motion_threshold = float(motion_thresholds.get(purpose))
            except Exception:
                chosen_motion_threshold = min_motion
        motion_val = v.get("motion_score")
        if motion_val is None:
            motion_val = 0.0
        if not reason and motion_val < chosen_motion_threshold:
            reason = f"low_motion({v.get('motion_score')})"
        if not reason and v.get("model_confidence") is not None and v.get("model_confidence", 0) < min_model_confidence:
            reason = f"low_model_confidence({v.get('model_confidence')})"
        if not reason and v.get("purpose") in require_faces_for and v.get("faces_detected", 0) <= 0:
            reason = "no_faces_for_person_purpose"

        if reason:
            r = v.copy()
            r["prune_reason"] = reason
            removed.append(r)
        else:
            kept.append(v)

    return kept, removed
