"""Command-line entry point for the fully integrated content factory."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from ai_video_factory.complete_factory import PLATFORM_LAYOUTS, run_autonomous_queue, run_complete_factory
from ai_video_factory.content_factory import queue_jobs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Edit Factory — complete production/content factory")
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("make", help="Topic in → upload-ready content package out")
    make.add_argument("topic")
    make.add_argument("--raw-video", required=False, default=None)
    make.add_argument("--out", default="output")
    make.add_argument("--target-seconds", type=float, default=45.0)
    make.add_argument("--platforms", nargs="+", default=list(PLATFORM_LAYOUTS), choices=list(PLATFORM_LAYOUTS))
    make.add_argument("--clip-count", type=int, default=None)
    make.add_argument("--experiments", default=None, help="JSON learning-history file")
    make.add_argument("--no-research", action="store_true")
    make.add_argument("--model-key", default=None)
    make.add_argument("--skip-qc", action="store_true")
    make.add_argument("--youtube-upload", action="store_true", help="Explicitly publish to YouTube")
    make.add_argument("--youtube-client-secrets", default=None)
    make.add_argument("--youtube-token-path", default=None)
    make.add_argument("--youtube-privacy", default="private", choices=["private", "unlisted", "public"])
    make.add_argument("--youtube-playlist", default=None)
    make.add_argument("--youtube-playlist-description", default="")
    make.add_argument("--disclosure-reviewed", action="store_true")
    make.add_argument("--ai-generated", action="store_true")
    make.add_argument("--realistic-alteration", action="store_true")

    q = sub.add_parser("queue", help="Create an autonomous content queue")
    q.add_argument("--topics", nargs="+", required=True)
    q.add_argument("--queue-file", default="output/content_queue.json")
    q.add_argument("--platform", default="youtube_shorts", choices=list(PLATFORM_LAYOUTS))

    runq = sub.add_parser("run-queue", help="Run the autonomous queue")
    runq.add_argument("--queue-file", required=True)
    runq.add_argument("--raw-video", required=False, default=None)
    runq.add_argument("--out", default="output/queue")
    runq.add_argument("--experiments", default=None)
    runq.add_argument("--max-jobs", type=int, default=None)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "queue":
        payload = queue_jobs(args.queue_file, args.topics, platform=args.platform)
        Path(args.queue_file).parent.mkdir(parents=True, exist_ok=True)
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "run-queue":
        result = run_autonomous_queue(
            args.queue_file,
            output_root=args.out,
            input_video=args.raw_video,
            experiment_history_path=args.experiments,
            max_jobs=args.max_jobs,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0 if all(job.get("status") == "complete" for job in result.get("jobs", [])) else 1

    job_name = "_".join(args.topic.split())[:80] or "job"
    package_dir = os.path.join(args.out, job_name)
    upload = None
    if args.youtube_upload:
        upload = {
            "privacy": args.youtube_privacy,
            "client_secrets_path": args.youtube_client_secrets,
            "token_path": args.youtube_token_path,
            "playlist_title": args.youtube_playlist,
            "playlist_description": args.youtube_playlist_description,
            "disclosure_reviewed": args.disclosure_reviewed,
            "ai_generated": args.ai_generated,
            "realistic_alteration": args.realistic_alteration,
        }
    result = run_complete_factory(
        args.raw_video,
        args.topic,
        package_dir,
        target_seconds=args.target_seconds,
        platforms=args.platforms,
        clip_count=args.clip_count,
        experiment_history_path=args.experiments,
        auto_research=not args.no_research,
        publish_youtube=args.youtube_upload,
        youtube_options=upload,
        model_key=args.model_key,
        skip_qc=args.skip_qc,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("status") == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
