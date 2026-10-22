"""CLI for producing multiple independent Shorts from one source video."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .batch_clip_factory import BatchClipError, run_batch_clip_factory


_PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels", "square", "youtube")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aivf-batch-clips",
        description=(
            "Find and produce several non-overlapping Shorts from one existing video. "
            "Each clip gets its own V3 package and quality checks."
        ),
    )
    parser.add_argument("input_video", help="Path to the existing source video")
    parser.add_argument("topic", help="Topic or story brief used by V3 for each candidate")
    parser.add_argument("--output-dir", required=True, help="New or empty output directory for this batch")
    parser.add_argument("--count", type=int, default=5, help="Maximum number of candidates (1-10; default: 5)")
    parser.add_argument("--seconds", type=float, default=25.0, help="Target candidate length in seconds (8-180; default: 25)")
    parser.add_argument("--gap", type=float, default=1.0, help="Minimum gap between source intervals in seconds (default: 1)")
    parser.add_argument("--platform", default="youtube_shorts", choices=_PLATFORMS)
    parser.add_argument("--audience", default="general short-form viewers")
    parser.add_argument("--bpm", type=int, default=120)
    parser.add_argument("--edit-type", default=None, help="Optional V3 edit-type override")
    parser.add_argument("--model-key", default=None, help="Optional configured model selector")
    parser.add_argument("--music", default=None, help="Optional music file used for each output")
    parser.add_argument("--enable-ocr", action="store_true", help="Include optional OCR in scene analysis")
    parser.add_argument(
        "--transcript",
        default=None,
        help="Optional timestamped .srt/.vtt subtitle file for topic-aware moment selection",
    )
    parser.add_argument("--no-object-detection", action="store_true", help="Disable optional object-detection stage")
    parser.add_argument("--enable-diarization", action="store_true", help="Enable optional speaker diarization")
    parser.add_argument("--diarization-token", default=None, help="Token for a configured diarization provider")
    parser.add_argument("--json", action="store_true", help="Print the complete batch manifest as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_batch_clip_factory(
            args.input_video,
            args.topic,
            Path(args.output_dir),
            count=args.count,
            clip_seconds=args.seconds,
            min_gap_seconds=args.gap,
            platform=args.platform,
            audience=args.audience,
            bpm=args.bpm,
            edit_type=args.edit_type,
            model_key=args.model_key,
            music_path=args.music,
            enable_ocr=args.enable_ocr,
            transcript_path=args.transcript,
            enable_object_detection=not args.no_object_detection,
            enable_diarization=args.enable_diarization,
            diarization_token=args.diarization_token,
        )
    except (BatchClipError, OSError, ValueError) as exc:
        print(f"Batch clip factory failed: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        summary = report.get("summary") or {}
        print(f"Batch status: {report.get('status')}")
        print(f"Successful: {summary.get('successful', 0)}")
        print(f"Failed: {summary.get('failed', 0)}")
        print(f"Manifest: {Path(args.output_dir).expanduser().resolve() / 'batch_manifest.json'}")
        for candidate in report.get("candidates", []):
            print(
                f"- {candidate.get('candidate_id')}: {candidate.get('status')} "
                f"(score {candidate.get('score', 0):.2f}/100)"
            )
            if candidate.get("final_video"):
                print(f"  {candidate['final_video']}")
            if candidate.get("error"):
                print(f"  {candidate['error']}")

    if report.get("status") == "complete":
        return 0
    if report.get("status") == "partial":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
