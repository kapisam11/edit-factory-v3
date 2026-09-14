import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Any

# Ensure the workspace root is on sys.path so local package imports work when running as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_video_factory.visuals_fetcher import prune_visuals


def load_visuals(pkg_dir: Path):
    vfile = pkg_dir / 'visuals.json'
    if vfile.exists():
        return json.loads(vfile.read_text(encoding='utf-8'))
    # fallback: look for visuals/*.json files and aggregate
    vis_dir = pkg_dir / 'visuals'
    if vis_dir.exists() and vis_dir.is_dir():
        items = []
        for p in sorted(vis_dir.glob('*.json')):
            try:
                j = json.loads(p.read_text(encoding='utf-8'))
                # support either a single object or list
                if isinstance(j, list):
                    items.extend(j)
                elif isinstance(j, dict):
                    items.append(j)
            except Exception:
                continue
        if items:
            return items
    raise FileNotFoundError(f'visuals.json or visuals/*.json not found in {pkg_dir}')


def run_dry(pkg_dir: Path, min_match: float, min_motion: float, motion_thresholds: Dict[str, float]):
    visuals = load_visuals(pkg_dir)
    kept, removed = prune_visuals(visuals, min_match_score=min_match, min_motion=min_motion, motion_thresholds=motion_thresholds)
    out = {
        'package': str(pkg_dir),
        'input_count': len(visuals),
        'kept_count': len(kept),
        'removed_count': len(removed),
        'removed': removed,
    }
    # write preview file
    out_path = pkg_dir / 'visuals_pruned_preview.json'
    out_path.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(f"Dry run complete. Kept: {len(kept)} Removed: {len(removed)} -> {out_path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument('package_dir', help='path to package folder containing visuals.json')
    p.add_argument('--min-match', type=float, default=0.2)
    p.add_argument('--min-motion', type=float, default=0.07)
    p.add_argument('--motion-thresholds', default='{}', help='JSON map of purpose->threshold')
    args = p.parse_args()
    mt = json.loads(args.motion_thresholds)
    run_dry(Path(args.package_dir), args.min_match, args.min_motion, mt)


if __name__ == '__main__':
    main()
