"""Command-line diagnostics for fresh-machine and media preflight checks."""
from __future__ import annotations

import argparse
import json
import sys

from ai_video_factory.media_health import MediaHealthError, analyze_media
from ai_video_factory.system_diagnostics import diagnostics_report

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Edit Factory environment and media diagnostics')
    parser.add_argument('--media', help='media file to inspect')
    parser.add_argument('--deep', action='store_true', help='run black/freeze/silence/audio analysis')
    parser.add_argument('--directory', action='append', default=[], help='runtime directory to check')
    args = parser.parse_args(argv)
    report = diagnostics_report(directories=args.directory)
    if args.media:
        try:
            report['media'] = analyze_media(args.media, deep=args.deep)
        except (MediaHealthError, OSError, ValueError) as exc:
            report['media'] = {'ok': False, 'error': str(exc)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get('ok', False) and report.get('media', {'ok': True}).get('ok', True) else 1

if __name__ == '__main__':
    raise SystemExit(main())