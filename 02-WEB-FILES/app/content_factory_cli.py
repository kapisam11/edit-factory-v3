"""One-click command surface for the complete content factory roadmap."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai_video_factory.content_factory import (
    competitor_content_gap,
    generate_chapters,
    generate_clip_factory_plan,
    generate_content_strategy,
    generate_hook_candidates,
    learn_channel_style,
    long_and_short_plan,
    performance_feedback,
    queue_jobs,
    thumbnail_factory_plan,
    trend_research,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Edit Factory content-production automation tools")
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("make", help="Plan an upload-ready content package")
    make.add_argument("topic")
    make.add_argument("--out", default="output/content-factory.json")
    make.add_argument("--platform", default="youtube_shorts")
    make.add_argument("--trends", action="store_true")
    make.add_argument("--competitors", default=None)
    make.add_argument("--clips", type=int, default=5)
    make.add_argument("--thumbnail-variants", default=None, help="JSON file of thumbnail feature rows")
    strategy = sub.add_parser("strategy", help="Generate content angles")
    strategy.add_argument("topic")
    strategy.add_argument("--count", type=int, default=10)
    trends = sub.add_parser("trends", help="Fetch current public trend headlines")
    trends.add_argument("topic")
    trends.add_argument("--limit", type=int, default=10)
    gap = sub.add_parser("gap", help="Analyze competitor/content gaps")
    gap.add_argument("topic")
    gap.add_argument("competitors_json")
    queue = sub.add_parser("queue", help="Create an autonomous production queue")
    queue.add_argument("topics", nargs="+")
    queue.add_argument("--out", default="output/content-queue.json")
    queue.add_argument("--platform", default="youtube_shorts")
    learn = sub.add_parser("learn-channel", help="Build a channel-style profile")
    learn.add_argument("videos_json")
    learn.add_argument("--out", default="output/channel-style.json")
    feedback = sub.add_parser("feedback", help="Normalize published performance")
    feedback.add_argument("metrics_json")
    feedback.add_argument("--out", default="output/performance-feedback.json")
    publish = sub.add_parser("publish", help="Explicitly publish an upload package to YouTube")
    publish.add_argument("package_dir")
    publish.add_argument("--client-secrets", required=True)
    publish.add_argument("--token-path", default=None)
    publish.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    publish.add_argument("--playlist-id", default=None)
    publish.add_argument("--disclosure-reviewed", action="store_true")
    return parser


def _write(path: str, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(str(target))


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "strategy":
        print(json.dumps(generate_content_strategy(args.topic, count=args.count), indent=2))
    elif args.command == "trends":
        print(json.dumps(trend_research(args.topic, limit=args.limit), indent=2))
    elif args.command == "gap":
        rows = json.loads(Path(args.competitors_json).read_text(encoding="utf-8"))
        print(json.dumps(competitor_content_gap(args.topic, candidates=rows), indent=2))
    elif args.command == "queue":
        _write(args.out, queue_jobs(args.out, args.topics, platform=args.platform))
    elif args.command == "learn-channel":
        rows = json.loads(Path(args.videos_json).read_text(encoding="utf-8"))
        _write(args.out, learn_channel_style(rows))
    elif args.command == "feedback":
        metrics = json.loads(Path(args.metrics_json).read_text(encoding="utf-8"))
        _write(args.out, performance_feedback(metrics))
    elif args.command == "make":
        competitors = None
        if args.competitors:
            competitors = json.loads(Path(args.competitors).read_text(encoding="utf-8"))
        thumbnails = []
        if args.thumbnail_variants:
            thumbnails = json.loads(Path(args.thumbnail_variants).read_text(encoding="utf-8"))
        strategy = generate_content_strategy(args.topic)
        payload: Dict[str, Any] = {
            "topic": args.topic,
            "platform": args.platform,
            "strategy": strategy,
            "selected_angle": strategy[0] if strategy else None,
            "trends": trend_research(args.topic) if args.trends else {"available": False, "skipped": True},
            "competitor_gap": competitor_content_gap(args.topic, candidates=competitors),
            "hooks": generate_hook_candidates(args.topic, strategy[0] if strategy else {}),
            "long_short": long_and_short_plan(args.topic),
            "clip_factory": generate_clip_factory_plan([], count=args.clips),
            "chapters": generate_chapters([]),
            "thumbnail_ranking": thumbnail_factory_plan(thumbnails),
        }
        _write(args.out, payload)
    elif args.command == "publish":
        from ai_video_factory.youtube_publisher import add_video_to_playlist, upload_video
        root = Path(args.package_dir)
        metadata = json.loads((root / "upload" / "metadata.json").read_text(encoding="utf-8"))
        result = upload_video(
            str(root / "final.mp4"),
            title=str(metadata.get("selected_title", root.name)),
            description=str(metadata.get("description", "")),
            tags=metadata.get("tags", []),
            privacy_status=args.privacy,
            thumbnail_path=metadata.get("files", {}).get("thumbnail") and str(root / metadata["files"]["thumbnail"]),
            caption_path=metadata.get("files", {}).get("captions") and str(root / metadata["files"]["captions"]),
            client_secrets_path=args.client_secrets,
            token_path=args.token_path,
            ai_generated=bool(metadata.get("ai_disclosure", {}).get("review_required", False)),
            realistic_alteration=bool(metadata.get("ai_disclosure", {}).get("review_required", False)),
            disclosure_reviewed=args.disclosure_reviewed,
        )
        if args.playlist_id:
            result["playlist"] = add_video_to_playlist(result["video_id"], args.playlist_id, client_secrets_path=args.client_secrets, token_path=args.token_path)
        _write(str(root / "youtube_publish.json"), result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
