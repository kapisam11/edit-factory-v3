"""Generate and validate a bounded exotic-media compatibility fixture matrix."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .compatibility_matrix import classify_media


def _run(args: list[str], timeout: int = 180) -> None:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("ffmpeg is required")
    result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout)[-3000:])


def generate_fixtures(root: str | Path) -> list[Path]:
    base = Path(root)
    base.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    specs = [
        ("cfr.mp4", ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-t", "2"]),
        ("vfr.mp4", ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-t", "2", "-vf", "select='if(lt(n,20),1,not(mod(n,3)))',setpts='if(lt(N,20),N/(30*TB),(20+(N-20)*1.5)/(30*TB))'", "-fps_mode", "vfr"]),
        ("ten_bit.mkv", ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24", "-t", "2", "-vf", "format=yuv420p10le", "-c:v", "libx264"]),
        ("interlaced.mp4", ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-t", "2", "-vf", "tinterlace=mode=interleave_top"]),
    ]
    for name, common in specs:
        target = base / name
        args = ["-hide_banner", "-loglevel", "error", "-y", *common]
        if target.suffix.lower() == ".mp4":
            args += ["-pix_fmt", "yuv420p", "-c:v", "libx264"]
        args.append(str(target))
        try:
            _run(args)
        except RuntimeError:
            continue
        if target.is_file() and target.stat().st_size:
            outputs.append(target)
    return outputs


def validate_fixture_matrix(paths: Iterable[str | Path]) -> dict[str, Any]:
    items = [classify_media(path) for path in paths]
    return {
        "count": len(items),
        "passed": sum(bool(item.get("ok")) for item in items),
        "failed": sum(not bool(item.get("ok")) for item in items),
        "items": items,
    }


__all__ = ["generate_fixtures", "validate_fixture_matrix"]
