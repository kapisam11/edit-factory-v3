"""Command-line entry point for the Edit Factory v3 emotion-first planner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .v3_engine import V3Config, blueprint_summary, create_v3_blueprint, validate_blueprint


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aivf-v3",
        description="Build an emotion-first short-form editing blueprint before rendering.",
    )
    parser.add_argument("topic", help="Person, topic, event, or story to edit")
    parser.add_argument("--context", default="", help="Facts/context used to select the emotional angle")
    parser.add_argument("--platform", default="youtube_shorts", choices=["youtube_shorts", "tiktok", "instagram_reels", "square", "youtube"])
    parser.add_argument("--seconds", type=float, default=30.0, help="Target duration (8-180 seconds)")
    parser.add_argument("--bpm", type=int, default=120, help="Music BPM used for beat-grid planning")
    parser.add_argument("--edit-type", default=None, help="Override the automatically selected edit type")
    parser.add_argument("--audience", default="general short-form viewers")
    parser.add_argument("--retention-interval", type=float, default=2.0)
    parser.add_argument("--output", default="v3_blueprint.json", help="JSON output path")
    parser.add_argument("--no-validate", action="store_true", help="Write draft blueprint even when QC does not pass")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = V3Config(
        target_seconds=args.seconds,
        platform=args.platform,
        audience=args.audience,
        bpm=args.bpm,
        retention_interval=args.retention_interval,
    )
    blueprint = create_v3_blueprint(args.topic, context=args.context, config=config, edit_type=args.edit_type)
    if not args.no_validate:
        validate_blueprint(blueprint)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(blueprint.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(blueprint_summary(blueprint))
    print(f"Blueprint: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
