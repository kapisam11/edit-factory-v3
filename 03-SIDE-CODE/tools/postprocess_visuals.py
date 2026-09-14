"""Post-process downloaded visuals: face detection and motion/scene sampling.

Usage: run from repo root. Points to a visuals directory and updates visuals.json
with additional fields: `faces_detected`, `motion_score`.

OpenCV is optional. Install project dependencies during environment setup; this
maintenance script never installs Python packages at runtime.
"""
import os
import sys
import json
import subprocess
import shutil
import math


def ensure_opencv():
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def detect_faces_in_image(img_path, face_cascade):
    import cv2
    img = cv2.imread(img_path)
    if img is None:
        return 0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4)
    return len(faces)


def sample_video_and_detect(video_path, tmp_dir, face_cascade, fps=1):
    # extract frames at given fps to tmp_dir
    os.makedirs(tmp_dir, exist_ok=True)
    out_pattern = os.path.join(tmp_dir, "frame_%04d.jpg")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vf", f"fps={fps}", out_pattern]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    except Exception:
        return 0, 0.0
    # detect faces across frames
    faces_total = 0
    frames = sorted([os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if f.lower().endswith('.jpg')])
    for f in frames:
        faces_total += detect_faces_in_image(f, face_cascade)
    motion_score = compute_motion_score_from_frames(frames)
    # cleanup
    try:
        shutil.rmtree(tmp_dir)
    except Exception:
        pass
    return faces_total, motion_score


def compute_motion_score_from_frames(frames):
    # simple motion proxy: average histogram difference between consecutive frames
    try:
        import cv2
        import numpy as np
    except Exception:
        return 0.0
    if len(frames) < 2:
        return 0.0
    prev = None
    diffs = []
    for f in frames:
        img = cv2.imread(f)
        if img is None:
            continue
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        if prev is not None:
            # Bhattacharyya distance
            d = cv2.compareHist(prev, hist, cv2.HISTCMP_BHATTACHARYYA)
            diffs.append(d)
        prev = hist
    if not diffs:
        return 0.0
    # map to 0-1 (higher means more motion/change)
    avg = sum(diffs) / len(diffs)
    score = max(0.0, min(1.0, avg))
    return float(score)


def main(visuals_dir: str):
    if not os.path.exists(visuals_dir):
        print('visuals dir not found:', visuals_dir)
        return
    has_cv = ensure_opencv()
    if not has_cv:
        print('Warning: opencv not available; face detection skipped. Install it with the project dependencies if needed.')
    # load visuals.json
    vjson = os.path.join(visuals_dir, 'visuals.json')
    if not os.path.exists(vjson):
        # try to list images
        items = []
        for fn in sorted(os.listdir(visuals_dir)):
            items.append({'local_path': os.path.join(visuals_dir, fn)})
    else:
        with open(vjson, 'r', encoding='utf-8') as f:
            try:
                items = json.load(f)
            except Exception:
                items = []

    updated = []
    face_cascade = None
    if has_cv:
        import cv2
        try:
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        except Exception:
            face_cascade = None

    for it in items:
        lp = it.get('local_path') or it.get('local_thumbnail') or it.get('thumbnail') or it.get('url')
        if not lp:
            updated.append(it)
            continue
        # if it's a remote url and not downloaded, skip
        if lp.startswith('http'):
            updated.append(it)
            continue
        if not os.path.exists(lp):
            updated.append(it)
            continue
        name = lp.lower()
        faces = 0
        motion = 0.0
        if has_cv and face_cascade and any(name.endswith(ext) for ext in ('.jpg', '.jpeg', '.png')):
            try:
                faces = detect_faces_in_image(lp, face_cascade)
            except Exception:
                faces = 0
        elif has_cv and face_cascade and name.endswith('.mp4'):
            tmp = os.path.join(visuals_dir, 'tmp_frames')
            try:
                faces, motion = sample_video_and_detect(lp, tmp, face_cascade, fps=1)
            except Exception:
                faces = 0
        else:
            # compute a quick motion proxy for images via tiny blur variance
            try:
                import cv2
                img = cv2.imread(lp, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    motion = float(cv2.Laplacian(img, cv2.CV_64F).var()) / 1000.0
                    motion = max(0.0, min(1.0, motion))
            except Exception:
                motion = 0.0

        it['faces_detected'] = int(faces)
        it['motion_score'] = float(motion)
        updated.append(it)

    # write back visuals.json
    try:
        with open(vjson, 'w', encoding='utf-8') as f:
            json.dump(updated, f, indent=2)
        print('Updated visuals.json with face and motion info')
    except Exception as e:
        print('Failed to write visuals.json:', e)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python tools/postprocess_visuals.py <visuals_dir>')
        sys.exit(1)
    main(sys.argv[1])
