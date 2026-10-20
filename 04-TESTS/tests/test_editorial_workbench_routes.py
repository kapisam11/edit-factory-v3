from __future__ import annotations

import importlib
import json
from pathlib import Path


SRT = (
    "1\n00:00:00,000 --> 00:00:01,000\nOpening line\n\n"
    "2\n00:00:01,000 --> 00:00:02,000\nPayoff line\n"
)


def _load_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_ENV", "test")
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIVF_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-editorial-review-secret")
    import web_app_v3
    importlib.reload(web_app_v3)
    web_app_v3.app.secret_key = "editorial-review-test-secret"
    import dashboard_compat
    dashboard_compat.register_dashboard_compat(web_app_v3.app)
    return web_app_v3


def test_editorial_review_route_exposes_preview_and_preserves_it_after_save(monkeypatch, tmp_path: Path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    package = Path(appmod.OUTPUT_FOLDER) / "review-route-fixture"
    package.mkdir(parents=True, exist_ok=True)
    (package / "final.v3.mp4").write_bytes(b"not-a-real-video-route-fixture")
    (package / "captions.srt").write_text(SRT, encoding="utf-8")
    (package / "clip_source_evidence.json").write_text(
        json.dumps([{
            "clip_index": 1,
            "status": "supported",
            "purpose": "Hook",
            "source_scene": "scene-1",
            "evidence_score": 0.81,
            "relevance_score": 0.79,
            "editorial_judgement": {
                "overall": 81.0,
                "dimensions": {"hook_strength": 90.0, "pacing": 72.0},
                "reasons": ["strong hook"],
            },
            "candidate_rankings": [],
        }]),
        encoding="utf-8",
    )

    client = appmod.app.test_client()
    page = client.get("/editorial-review/review-route-fixture")
    assert page.status_code == 200
    assert b"Editorial Review Workbench" in page.data
    assert b"Editorial decision report" in page.data
    evidence = client.get("/api/package/review-route-fixture/file/clip_source_evidence.json")
    assert evidence.status_code == 200
    assert evidence.json[0]["editorial_judgement"]["dimensions"]["hook_strength"] == 90.0

    first = client.get("/api/package/review-route-fixture/review")
    assert first.status_code == 200
    assert first.json["video_url"]
    assert first.json["captions_srt"] == SRT

    saved = client.post(
        "/api/package/review-route-fixture/review",
        json={"title": "Corrected title", "description": "Corrected description", "captions_srt": SRT.replace("Opening line", "Better opening")},
    )
    assert saved.status_code == 200
    review = saved.json["review"]
    assert review["video_url"]
    assert review["version"] == 1
    assert review["render_status"] == "requires_rerender"
    assert (package / "final.v3.mp4").read_bytes() == b"not-a-real-video-route-fixture"

    restored = client.post("/api/package/review-route-fixture/review/restore/1")
    assert restored.status_code == 200
    assert restored.json["review"]["version"] == 2
    assert restored.json["review"]["video_url"]


def test_preset_history_versions_and_legacy_migration(monkeypatch, tmp_path: Path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    client = appmod.app.test_client()

    created = client.post("/api/presets", json={"name": "test-preset", "config": {"target_seconds": 30}})
    assert created.status_code == 201
    assert created.json["version"] == 1

    updated = client.post("/api/presets", json={"name": "test-preset", "config": {"target_seconds": 45}})
    assert updated.status_code == 201
    assert updated.json["version"] == 2

    history = client.get("/api/presets/test-preset/history")
    assert history.status_code == 200
    assert [item["version"] for item in history.json["history"]] == [1, 2]

    restored = client.post("/api/presets/test-preset/restore/1")
    assert restored.status_code == 200
    assert restored.json["version"] == 3
    presets = client.get("/api/presets")
    assert presets.status_code == 200
    assert presets.json["test-preset"]["target_seconds"] == 30

    with appmod.get_db() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?)",
            ("preset:legacy-preset", json.dumps({"target_seconds": 20})),
        )
    migrated = client.post("/api/presets", json={"name": "legacy-preset", "config": {"target_seconds": 25}})
    assert migrated.status_code == 201
    assert migrated.json["version"] == 2
    legacy_history = client.get("/api/presets/legacy-preset/history")
    assert [item["version"] for item in legacy_history.json["history"]] == [1, 2]


def test_editorial_review_rating_updates_existing_local_learning_history(monkeypatch, tmp_path: Path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    package = Path(appmod.OUTPUT_FOLDER) / "rated-package"
    package.mkdir(parents=True, exist_ok=True)
    history_path = Path(appmod.STATE_DIR) / "learning_history.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps([{
            "package_dir": str(package.resolve()),
            "topic": "test topic",
            "duration_seconds": 30,
            "caption_style": "karaoke",
            "user_rating": 3,
        }]),
        encoding="utf-8",
    )

    client = appmod.app.test_client()
    initial = client.get("/api/package/rated-package/rating")
    assert initial.status_code == 200
    assert initial.json["rating"] == 3

    saved = client.post(
        "/api/package/rated-package/rating",
        json={"rating": 5, "note": "Excellent hook and captions"},
    )
    assert saved.status_code == 200
    assert saved.json["rating"] == 5

    persisted = json.loads(history_path.read_text(encoding="utf-8"))
    assert persisted[0]["user_rating"] == 5
    assert persisted[0]["user_feedback"] == "Excellent hook and captions"
    assert persisted[0]["user_feedback_at"]


def test_review_render_endpoint_returns_separate_rendered_video_url(monkeypatch, tmp_path: Path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    package = Path(appmod.OUTPUT_FOLDER) / "rendered-review-package"
    package.mkdir(parents=True, exist_ok=True)
    (package / "final.v3.mp4").write_bytes(b"original-video")
    (package / "captions.srt").write_text(SRT, encoding="utf-8")
    from ai_video_factory.review_workbench import load_review, save_review
    seed = load_review(package)
    state = save_review(package, {**seed, "captions_srt": SRT.replace("Opening line", "Corrected opening")})

    def fake_render(target):
        renders = Path(target) / "reviewed" / "renders"
        renders.mkdir(parents=True, exist_ok=True)
        output = renders / "final.reviewed-v1-test.mp4"
        output.write_bytes(b"corrected-video")
        return {
            **state,
            "render_status": "rendered",
            "rendered_for_version": state["version"],
            "rendered_video_file": "reviewed/renders/final.reviewed-v1-test.mp4",
            "rendered_video_sha256": "test-sha",
        }

    import ai_video_factory.review_workbench as workbench
    monkeypatch.setattr(workbench, "render_reviewed_video", fake_render)

    client = appmod.app.test_client()
    response = client.post("/api/package/rendered-review-package/review/render")

    assert response.status_code == 200
    assert response.json["review"]["reviewed_video_url"].endswith(
        "/reviewed/renders/final.reviewed-v1-test.mp4"
    )
    assert response.json["review"]["video_url"].endswith("/final.v3.mp4")
    assert (package / "final.v3.mp4").read_bytes() == b"original-video"
