"""Motion-graphics template utilities.

Support loading PNG overlay templates and applying them to clips using ffmpeg.
Templates should be simple PNGs with transparency designed to be overlaid on top
of the vertical 1080x1920 frames.
"""
import os
import subprocess
import tempfile
from typing import List
from . import psd_utils


def find_templates(search_dirs: List[str] = None) -> List[str]:
    """Return a list of overlay template paths found under the provided dirs.

    Defaults to the extension prompt library and the web dashboard templates.
    """
    if search_dirs is None:
        search_dirs = [
            "05-EXTENSIONS/prompt-library/overlays",
            "05-EXTENSIONS/prompt-library",
            "templates/overlays",
            "templates",
        ]
    found = []
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.lower().endswith((".png", ".webp")):
                found.append(os.path.join(d, f))
    return found


def apply_overlay(input_clip: str, overlay_png: str, out_clip: str) -> None:
    """Apply a single overlay PNG to `input_clip` and write `out_clip`.

    The overlay is scaled to fit width 1080 and centered vertically. Uses ffmpeg
    through an argv list so filenames can never be interpreted as shell syntax.
    """
    tmp_png = None
    try:
        if overlay_png.lower().endswith('.psd'):
            fd, tmp_png = tempfile.mkstemp(suffix='.png')
            os.close(fd)
            psd_utils.composite_psd(overlay_png, tmp_png)
            overlay_to_use = tmp_png
        else:
            overlay_to_use = overlay_png

        cmd = [
            "ffmpeg", "-y",
            "-i", input_clip,
            "-i", overlay_to_use,
            "-filter_complex", "[1]scale=1080:-1[ov];[0][ov]overlay=(W-w)/2:(H-h)/2",
            "-c:v", "libx264",
            "-c:a", "copy",
            out_clip,
        ]
        subprocess.run(cmd, check=True, timeout=3600)
    finally:
        if tmp_png and os.path.exists(tmp_png):
            try:
                os.remove(tmp_png)
            except OSError:
                pass
