"""Visual hook generator based on package visuals data."""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

WEIGHTS: Dict[str, float] = {
    "faces_weight": 2.0,
    "motion_weight": 1.5,
    "match_weight": 1.0,
    "label_boost": 1.5,
    "center_boost": 2.0,
}

try:
    wpath = Path("05-EXTENSIONS/hook-scoring/weights.json")
    if wpath.exists():
        WEIGHTS.update(json.loads(wpath.read_text(encoding="utf-8")))
except Exception:
    pass


def load_visuals(pkg_dir: Path) -> List[Dict[str, Any]]:
    vfile = pkg_dir / "visuals" / "visuals.json"
    if vfile.exists():
        try:
            data = json.loads(vfile.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            return []

    vis_dir = pkg_dir / "visuals"
    items: List[Dict[str, Any]] = []
    if vis_dir.exists():
        for p in sorted(vis_dir.glob("*.json")):
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(j, list):
                    items.extend(j)
                elif isinstance(j, dict):
                    items.append(j)
            except Exception:
                continue
    return items


def strongest_visual(visuals: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not visuals:
        return None

    def score(v: Dict[str, Any]) -> float:
        faces = int(v.get("faces_detected") or 0)
        motion = float(v.get("motion_score") or 0.0)
        match_score = float(v.get("match_score") or 0.0)
        s = (
            faces * float(WEIGHTS.get("faces_weight", 2.0))
            + motion * float(WEIGHTS.get("motion_weight", 1.5))
            + match_score * float(WEIGHTS.get("match_weight", 1.0))
        )

        labels = v.get("object_labels") or v.get("labels") or []
        if labels:
            s += float(WEIGHTS.get("label_boost", 1.5))

        try:
            bbox = v.get("face_box") or v.get("face_bbox")
            iw = v.get("image_width")
            ih = v.get("image_height")
            if bbox and iw and ih:
                x, y, w, h = bbox
                cx = x + w / 2.0
                cy = y + h / 2.0
                dx = abs(cx - iw / 2.0) / (iw / 2.0)
                dy = abs(cy - ih / 2.0) / (ih / 2.0)
                dist = (dx + dy) / 2.0
                s += max(0.0, (1.0 - dist) * float(WEIGHTS.get("center_boost", 2.0)))
        except Exception:
            pass
        return s

    return max(visuals, key=score)


HOOK_TEMPLATES = [
    "His final choice",
    "Betrayal revealed",
    "Lost forever",
    "The real reason",
    "Nobody believed him",
    "The hidden reason",
    "Shocking moment",
]


def generate_from_visual(v: Dict[str, Any], personas: Optional[List[Dict[str, Any]]] = None) -> str:
    name: Optional[str] = None
    try:
        if personas:
            for p in personas:
                if p.get("name"):
                    name = str(p.get("name"))
                    break
    except Exception:
        name = None

    faces = int(v.get("faces_detected") or 0)
    motion = float(v.get("motion_score") or 0.0)
    desc = str(v.get("description") or v.get("title") or "")

    if faces >= 1 and motion > 0.05:
        if name and len(name.split()) <= 2:
            return f"{name}'s choice"
        return "His final choice"
    if motion > 0.2:
        return "Shocking moment"

    labels = v.get("object_labels") or v.get("labels") or []
    if labels:
        lab = labels[0] if isinstance(labels, list) and labels else None
        if lab:
            lab_name = str(lab).title()
            return f"{lab_name} revealed" if len(lab_name.split()) <= 2 else "Shocking moment"
    if "betray" in desc.lower() or "betray" in (v.get("tags") or []):
        return "Betrayal revealed"
    if "lost" in desc.lower():
        return "Lost forever"
    for t in HOOK_TEMPLATES:
        if 2 <= len(t.split()) <= 5:
            return t
    return "The real reason"


def generate_visual_hook(pkg_path: str) -> Optional[str]:
    pkg = Path(pkg_path)
    visuals = load_visuals(pkg)
    v = strongest_visual(visuals)
    personas: List[Dict[str, Any]] = []
    try:
        pfile = pkg / "personas.json"
        if pfile.exists():
            personas = json.loads(pfile.read_text(encoding="utf-8"))
    except Exception:
        personas = []
    if not v:
        return None
    return generate_from_visual(v, personas)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python tools/visual_hook_generator.py <package_path>")
        raise SystemExit(1)
    hook = generate_visual_hook(sys.argv[1])
    if hook:
        print(hook)
        raise SystemExit(0)
    raise SystemExit(2)
