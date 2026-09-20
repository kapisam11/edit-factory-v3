import io
import json
from pathlib import Path

import pytest


def _load_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIVF_OUTPUT_DIR", str(tmp_path / "output"))
    import importlib
    import web_app_v3
    importlib.reload(web_app_v3)
    return web_app_v3


def test_dashboard_queues_full_v3_configuration(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    captured = {}

    fake_upload = Path(appmod.UPLOAD_FOLDER) / "input.mp4"
    fake_upload.write_bytes(b"placeholder")

    def save_upload(_upload, _suffix):
        return fake_upload

    def start_job(job_id, params, secrets):
        captured.update(job_id=job_id, params=dict(params), secrets=dict(secrets))
        return True

    monkeypatch.setattr(appmod, "_save_and_validate_upload", save_upload)
    monkeypatch.setattr(appmod, "_start_job", start_job)

    response = appmod.app.test_client().post(
        "/api/jobs",
        data={
            "topic": "V3 dashboard integration",
            "workflow": "v3",
            "target_seconds": "60",
            "platform": "tiktok",
            "audience": "gaming viewers",
            "bpm": "150",
            "edit_type": "Dramatic",
            "context": "Start with the strongest moment.",
            "enable_ocr": "true",
            "enable_object_detection": "true",
            "enable_diarization": "true",
            "raw_video": (io.BytesIO(b"video"), "source.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    assert captured["params"]["workflow"] == "v3"
    assert captured["params"]["target_seconds"] == 60.0
    assert captured["params"]["platform"] == "tiktok"
    assert captured["params"]["audience"] == "gaming viewers"
    assert captured["params"]["bpm"] == 150
    assert captured["params"]["edit_type"] == "Dramatic"
    assert captured["params"]["enable_ocr"] is True
    assert captured["params"]["enable_object_detection"] is True
    assert captured["params"]["enable_diarization"] is True
    assert captured["params"]["raw_video"] == str(fake_upload)

    job = appmod.db_get_job(captured["job_id"])
    persisted = json.loads(job["params"])
    assert persisted["workflow"] == "v3"
    assert persisted["platform"] == "tiktok"


def test_dashboard_requires_video_for_v3(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    response = appmod.app.test_client().post(
        "/api/jobs",
        data={
            "topic": "V3 without footage",
            "workflow": "v3",
            "target_seconds": "30",
        },
    )
    assert response.status_code == 400
    assert "raw video" in response.get_json()["error"].lower()


def test_dashboard_rejects_v3_duration_outside_v3_contract(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    response = appmod.app.test_client().post(
        "/api/jobs",
        data={
            "topic": "Bad V3 duration",
            "workflow": "v3",
            "target_seconds": "200",
        },
    )
    assert response.status_code == 400
    assert "between 8 and 180" in response.get_json()["error"]
