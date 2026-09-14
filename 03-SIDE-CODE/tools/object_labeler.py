"""Populate `object_labels` for visuals in package folders.

Strategy:
- If `object_labels` already present, skip.
- Use heuristics from filename/title/url to guess labels.
- If OpenCV DNN MobileNetSSD model files are available under `.models/mobilenet_ssd/`, run detection on thumbnails.
"""
import json
from pathlib import Path
import re
import os

KEYWORDS = ['player', 'creeper', 'steve', 'skeleton', 'zombie', 'building', 'house', 'castle', 'chest', 'sword']


def heuristic_labels_from_text(text: str):
    text = (text or '').lower()
    labels = []
    for k in KEYWORDS:
        if k in text:
            labels.append(k)
    return labels


def run_local_detector(img_path: Path, model_dir: Path):
    try:
        import cv2
    except Exception:
        return []
    prototxt = model_dir / 'deploy.prototxt'
    model = model_dir / 'mobilenet.caffemodel'
    if not prototxt.exists() or not model.exists():
        return []
    net = cv2.dnn.readNetFromCaffe(str(prototxt), str(model))
    img = cv2.imread(str(img_path))
    if img is None:
        return []
    (h, w) = img.shape[:2]
    blob = cv2.dnn.blobFromImage(cv2.resize(img, (300, 300)), 0.007843, (300, 300), 127.5)
    net.setInput(blob)
    detections = net.forward()
    labels = []
    CLASSES = ["background","aeroplane","bicycle","bird","boat","bottle","bus","car","cat","chair","cow","diningtable","dog","horse","motorbike","person","pottedplant","sheep","sofa","train","tvmonitor"]
    for i in range(detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        if confidence > 0.5:
            idx = int(detections[0, 0, i, 1])
            if idx < len(CLASSES):
                labels.append(CLASSES[idx])
    return list(dict.fromkeys([l for l in labels if l != 'background']))


def process_package(pkg_dir: Path):
    vis_dir = pkg_dir / 'visuals'
    if not vis_dir.exists():
        return 0
    changed = 0
    model_dir = Path('.models/mobilenet_ssd')
    for vf in sorted(vis_dir.glob('*.json')):
        try:
            j = json.loads(vf.read_text(encoding='utf-8'))
        except Exception:
            continue
        if isinstance(j, list):
            items = j
        else:
            items = [j]
        updated = False
        for item in items:
            if item.get('object_labels'):
                continue
            labels = []
            # heuristic from title/url/local_thumbnail
            labels.extend(heuristic_labels_from_text(item.get('title') or ''))
            labels.extend(heuristic_labels_from_text(item.get('url') or ''))
            thumb = item.get('local_thumbnail')
            if thumb and Path(thumb).exists():
                # try model detection if available
                if model_dir.exists():
                    det = run_local_detector(Path(thumb), model_dir)
                    labels.extend(det)
            # dedupe
            labels = [l for i, l in enumerate(labels) if l and l not in labels[:i]]
            if labels:
                item['object_labels'] = labels
                updated = True
                changed += 1
        if updated:
            try:
                vf.write_text(json.dumps(items if isinstance(j, list) else items[0], indent=2), encoding='utf-8')
            except Exception:
                pass
    return changed


def main(root='.'): 
    rootp = Path(root)
    outs = [p for p in rootp.iterdir() if p.is_dir() and p.name.startswith('output')]
    total = 0
    for o in outs:
        for child in o.iterdir():
            if child.is_dir():
                c = process_package(child)
                if c:
                    print('Updated', child, '->', c)
                total += c
    print('Total labels added:', total)
    return total


if __name__ == '__main__':
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    main(root)
