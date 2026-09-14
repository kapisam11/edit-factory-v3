"""Export a simple EDL (edit decision list) so human editors can open
the rough-cut in their NLE for quick polish.
"""
import os
from typing import List


def export_edl(package_dir: str, seq_files: List[str], out_name: str = "sequence.edl") -> str:
    out_path = os.path.join(package_dir, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("TITLE: AI Video Factory Rough-Cut\n")
        f.write("FCM: NON-DROP FRAME\n\n")
        for idx, p in enumerate(seq_files, start=1):
            # simple EDL with reel, track, start, end, length
            fname = os.path.basename(p)
            f.write(f"{idx:03d}  AX       V     C        00:00:00:00 00:00:00:00 00:00:00:00\n")
            f.write(f"* FROM CLIP: {fname}\n\n")
    return out_path
