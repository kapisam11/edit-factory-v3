"""Learning pipeline: store feedback, extract features, optional train/predict.

This module records package-level examples and feedback into a local
SQLite database. It can train a simple classifier with scikit-learn if
available, or fall back to a heuristic scorer.
"""
import sqlite3
import os
from typing import Dict, Any, List
import json

DB_PATH = os.path.join(os.path.dirname(__file__), "learning.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        package TEXT,
        features TEXT,
        label INTEGER,
        meta TEXT
    )""")
    conn.commit()
    return conn


def record_feedback(package: str, features: Dict[str, Any], label: int, meta: Dict[str, Any] = None):
    """Store a labeled example. Label: 1 positive, 0 negative."""
    conn = _get_conn()
    conn.execute("INSERT INTO feedback (package, features, label, meta) VALUES (?, ?, ?, ?)",
                 (package, json.dumps(features), int(label), json.dumps(meta or {})))
    conn.commit()
    conn.close()


def list_examples(limit: int = 100) -> List[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.execute("SELECT id, package, features, label, meta FROM feedback ORDER BY id DESC LIMIT ?", (limit,))
    rows = []
    for r in cur.fetchall():
        rows.append({"id": r[0], "package": r[1], "features": json.loads(r[2]), "label": r[3], "meta": json.loads(r[4])})
    conn.close()
    return rows


def extract_features(package_path: str) -> Dict[str, Any]:
    """Compute simple features from a package used for learning.

    Features are lightweight heuristics: script length, number of titles,
    presence of thumbnail, subtitle count, approximate script readability.
    """
    feats: Dict[str, Any] = {}
    try:
        with open(os.path.join(package_path, "script.txt"), "r", encoding="utf-8") as f:
            script = f.read()
    except Exception:
        script = ""
    feats["script_chars"] = len(script)
    feats["script_lines"] = len([l for l in script.splitlines() if l.strip()])

    titles = 0
    try:
        with open(os.path.join(package_path, "title_options.txt"), "r", encoding="utf-8") as f:
            titles = len([l for l in f.readlines() if l.strip()])
    except Exception:
        titles = 0
    feats["title_count"] = titles

    feats["has_thumbnail"] = int(any(f.lower().startswith("thumbnail") for f in os.listdir(package_path)))
    feats["has_srt"] = int(os.path.exists(os.path.join(package_path, "script.srt")))
    feats["has_voice"] = int(any(f.lower().startswith("voiceover") for f in os.listdir(package_path)))

    # subtitle line count
    srt_count = 0
    try:
        with open(os.path.join(package_path, "script.srt"), "r", encoding="utf-8") as f:
            srt_count = len([l for l in f.readlines() if l.strip() and "-->" not in l and not l.strip().isdigit()])
    except Exception:
        srt_count = 0
    feats["srt_lines"] = srt_count

    return feats


def heuristic_score(features: Dict[str, Any]) -> float:
    """Return a heuristic quality score [0..1] based on features."""
    score = 0.0
    # script length (best between 80 and 800 chars)
    sc = features.get("script_chars", 0)
    score += max(0.0, min(1.0, (sc - 80) / 720.0)) * 0.4
    # titles
    score += min(1.0, features.get("title_count", 0) / 3.0) * 0.1
    # thumbnail
    score += 0.2 if features.get("has_thumbnail") else 0.0
    # srt and voice
    score += 0.15 if features.get("has_srt") else 0.0
    score += 0.15 if features.get("has_voice") else 0.0
    return min(1.0, score)


def train_model(save_path: str = None) -> str:
    """Train a simple classifier if scikit-learn is available.

    Returns path to saved model, or empty string if training skipped.
    """
    try:
        import joblib
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split
        import numpy as np
    except Exception:
        return ""

    rows = list_examples(10000)
    if len(rows) < 10:
        return ""

    X = []
    y = []
    for r in rows:
        feats = r["features"]
        X.append([feats.get("script_chars", 0), feats.get("script_lines", 0), feats.get("title_count", 0), feats.get("has_thumbnail", 0), feats.get("has_srt", 0), feats.get("has_voice", 0), feats.get("srt_lines", 0)])
        y.append(r["label"])

    X = np.array(X)
    y = np.array(y)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    clf = LogisticRegression(max_iter=1000)
    clf.fit(X_train, y_train)
    acc = clf.score(X_test, y_test)

    model_path = save_path or os.path.join(os.path.dirname(__file__), "model.joblib")
    joblib.dump({"model": clf, "acc": acc}, model_path)
    return model_path


def predict_quality(package_path: str) -> Dict[str, Any]:
    """Predict quality using trained model if available, otherwise heuristic."""
    feats = extract_features(package_path)
    model_file = os.path.join(os.path.dirname(__file__), "model.joblib")
    try:
        import joblib
        data = joblib.load(model_file)
        clf = data.get("model")
        import numpy as np
        X = [feats.get(k, 0) for k in ["script_chars", "script_lines", "title_count", "has_thumbnail", "has_srt", "has_voice", "srt_lines"]]
        prob = float(clf.predict_proba([X])[0][1])
        return {"score": prob, "method": "model"}
    except Exception:
        return {"score": heuristic_score(feats), "method": "heuristic", "features": feats}
