"""PSD layer utilities.

Extracts PNGs from PSD layers using `psd_tools` when available.
"""
import os
from typing import List


def extract_layers(psd_path: str, out_dir: str) -> List[str]:
    """Extract visible layers from `psd_path` into `out_dir` as PNGs.

    Returns list of generated PNG paths. Requires `psd_tools` to be installed.
    """
    os.makedirs(out_dir, exist_ok=True)
    try:
        from psd_tools import PSDImage
    except Exception:
        raise RuntimeError("psd_tools not installed; cannot extract PSD layers")

    psd = PSDImage.open(psd_path)
    out_files = []
    idx = 0
    for layer in psd.descendants():
        try:
            if not getattr(layer, "visible", True):
                continue
            # some layers may not have image data
            im = layer.topil()
            if im is None:
                continue
            name = layer.name or f"layer_{idx}"
            # sanitize name
            safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)[:80]
            out_path = os.path.join(out_dir, f"{idx:03d}_{safe}.png")
            im.save(out_path)
            out_files.append(out_path)
            idx += 1
        except Exception:
            continue
    return out_files


def composite_psd(psd_path: str, out_png: str):
    """Render the full PSD composite to `out_png` using psd_tools.

    Returns the path to the written PNG.
    """
    try:
        from psd_tools import PSDImage
    except Exception:
        raise RuntimeError("psd_tools not installed; cannot composite PSD")

    psd = PSDImage.open(psd_path)
    im = psd.composite()
    im.save(out_png)
    return out_png
