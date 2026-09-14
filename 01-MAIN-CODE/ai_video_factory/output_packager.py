"""Write the final upload package to disk."""
import json
import os
from typing import Dict


def write_package(base_dir: str, summary: Dict, idea: Dict, thumbnail_path: str) -> str:
    """Create output directory and write package files.

    Returns the path to the package folder.
    """
    os.makedirs(base_dir, exist_ok=True)
    # write research
    with open(os.path.join(base_dir, "research.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # write plan
    with open(os.path.join(base_dir, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(idea, f, indent=2, ensure_ascii=False)

    # write script
    with open(os.path.join(base_dir, "script.txt"), "w", encoding="utf-8") as f:
        f.write(idea.get("script", ""))

    # write title options
    with open(os.path.join(base_dir, "title_options.txt"), "w", encoding="utf-8") as f:
        for t in idea.get("title_options", []):
            f.write(t + "\n")

    # write description template
    desc = f"Short doc-style short about {summary.get('topic')}. Watch till the end to see the reveal."
    with open(os.path.join(base_dir, "description.txt"), "w", encoding="utf-8") as f:
        f.write(desc)

    # write tags and hashtags
    topic_text = summary.get("topic", "").strip()
    tags = [tag for tag in [topic_text, "gaming", "shorts", summary.get("content_type", "story")] if tag]
    hashtags = ["#Minecraft", "#SMP", "#Gaming", "#Shorts"]
    if summary.get("content_type") == "Minecraft":
        hashtags = ["#Minecraft", "#SMP", "#Gaming", "#Shorts"]
    else:
        hashtags = ["#Shorts", "#Story", "#Gaming", "#Viral"]
    with open(os.path.join(base_dir, "tags.txt"), "w", encoding="utf-8") as f:
        f.write(", ".join(tags))
    with open(os.path.join(base_dir, "hashtags.txt"), "w", encoding="utf-8") as f:
        f.write(" ".join(hashtags))

    social_metadata = {
        "title_options": idea.get("title_options", []),
        "description": (
            f"{idea.get('hook', '').strip()} - {summary.get('why_care', '')} "
            f"Watch till the end for the emotional payoff and the hidden reason."
        ),
        "tags": tags,
        "hashtags": hashtags,
        "platforms": [
            {"name": "YouTube Shorts", "aspect_ratio": "9:16", "resolution": "1080x1920", "target_length_sec": 30},
            {"name": "TikTok", "aspect_ratio": "9:16", "resolution": "1080x1920", "target_length_sec": 30},
            {"name": "Instagram Reels", "aspect_ratio": "9:16", "resolution": "1080x1920", "target_length_sec": 30},
        ],
        "why_this_works": (
            "Strong hook, emotional story arc, filmed pacing, and clear payoff make this edit feel human and high-retention."
        ),
        "emotion": idea.get("emotion", "dramatic"),
    }
    with open(os.path.join(base_dir, "social_package.json"), "w", encoding="utf-8") as f:
        json.dump(social_metadata, f, indent=2, ensure_ascii=False)

    # write a metadata manifest for quick upload review
    manifest = {
        "topic": summary.get("topic"),
        "content_type": summary.get("content_type"),
        "platform_focus": summary.get("platform_focus"),
        "trending": summary.get("trending"),
        "hook": idea.get("hook"),
        "emotion": idea.get("emotion"),
        "duration_sec": idea.get("structure", {}).get("total_seconds"),
        "thumbnail": os.path.basename(thumbnail_path) if thumbnail_path else None,
        "primary_visuals": [v.get("url") for v in summary.get("visuals", [])[:3]],
    }
    with open(os.path.join(base_dir, "package_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    # copy thumbnail into package location
    if thumbnail_path:
        dst = os.path.join(base_dir, os.path.basename(thumbnail_path))
        try:
            import shutil

            shutil.copyfile(thumbnail_path, dst)
        except Exception:
            pass

    return base_dir
