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
    monkeypatch.setattr(appmod, "_runtime_capabilities", lambda: {"ocr": True, "object_detection": True, "diarization": True})
    captured = {}

    fake_upload = Path(appmod.UPLOAD_FOLDER) / "input.mp4"
    fake_upload.write_bytes(b"placeholder")

    def save_upload(_upload, _suffix):
        return fake_upload

    def start_job(job_id, params, secrets):
        captured.update(job_id=job_id, params=dict(params), secrets=dict(secrets))
        return True

    monkeypatch.setattr(appmod, "_runtime_capabilities", lambda: {"ocr": True, "object_detection": True, "diarization": True})
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
            "diarization_token": "test-diarization-token",
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
    assert captured["secrets"]["diarization_token"] == "test-diarization-token"

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
            "platform": "tiktok",
        },
    )
    assert response.status_code == 400
    assert "between 8 and 180" in response.get_json()["error"]


def test_worker_dispatches_v3_to_real_v3_pipeline(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    from types import SimpleNamespace
    import ai_video_factory.v3_pipeline as v3_pipeline

    appmod.db_insert_job("job-v3-worker", "V3 worker", {
        "topic": "V3 worker",
        "workflow": "v3",
        "target_seconds": 30.0,
        "raw_video": str(tmp_path / "source.mp4"),
        "platform": "youtube_shorts",
        "audience": "general short-form viewers",
        "bpm": 120,
    })

    captured = {}

    def fake_run_v3_pipeline(input_video, topic, package_dir, **kwargs):
        captured.update(input_video=input_video, topic=topic, package_dir=package_dir, kwargs=kwargs)
        return SimpleNamespace(
            errors=[],
            warnings=["example warning"],
            artifacts={"v3_readiness": str(Path(package_dir) / "v3_readiness.json")},
            final_video=str(Path(package_dir) / "final.v3.mp4"),
        )

    monkeypatch.setattr(v3_pipeline, "run_v3_pipeline", fake_run_v3_pipeline)
    monkeypatch.setattr(appmod, "_artifact_is_valid", lambda _path: True)

    appmod._run_job_worker_impl(
        "job-v3-worker",
        {
            "topic": "V3 worker",
            "workflow": "v3",
            "target_seconds": 30.0,
            "raw_video": str(tmp_path / "source.mp4"),
            "platform": "youtube_shorts",
            "audience": "general short-form viewers",
            "bpm": 120,
        },
        {},
        str(tmp_path / "output"),
        str(appmod.DB_PATH),
    )

    assert captured["topic"] == "V3 worker"
    assert captured["kwargs"]["platform"] == "youtube_shorts"
    assert captured["kwargs"]["audience"] == "general short-form viewers"
    assert appmod.db_get_job("job-v3-worker")["status"] == "done"
    assert (Path(captured["package_dir"]) / "v3_job_result.json").is_file()


def test_v3_defaults_are_platform_safe(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    appmod.set_setting("default_workflow", "v3")
    appmod.set_setting("default_v3_platform", "youtube_shorts")
    appmod.set_setting("default_target_seconds", 120)
    settings = appmod.get_settings()
    assert settings["default_v3_platform"] == "youtube_shorts"
    assert settings["default_target_seconds"] <= 60

    with pytest.raises(ValueError):
        appmod.set_setting("default_v3_platform", "not-a-platform")


def test_legacy_idempotency_media_hash_can_use_persisted_provenance(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    package = Path(appmod.OUTPUT_FOLDER) / "legacy-job"
    package.mkdir(parents=True)
    (package / "provenance.json").write_text(
        json.dumps({
            "assets": [{"asset_id": "source_video", "sha256": "a" * 64}],
        }),
        encoding="utf-8",
    )
    assert appmod._persisted_input_media_hash(
        {"pkg_dir": str(package)},
        {},
    ) == "a" * 64


def test_dashboard_idempotency_fingerprint_includes_uploaded_media(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"video-one")
    second.write_bytes(b"video-two")

    first_hash = appmod._request_fingerprint(
        {"topic": "same", "workflow": "v3", "_principal": "alice"},
        first,
    )
    second_hash = appmod._request_fingerprint(
        {"topic": "same", "workflow": "v3", "_principal": "alice"},
        second,
    )
    assert first_hash != second_hash
    assert first_hash == appmod._request_fingerprint(
        {"topic": "same", "workflow": "v3", "_principal": "alice"},
        first,
    )


def test_build_job_params_carries_license_url():
    from app.job_service import build_job_params

    params = build_job_params(
        {
            "topic": "Licensed clip",
            "workflow": "v3",
            "target_seconds": "15",
            "platform": "youtube_shorts",
            "audience": "short-form viewers",
            "rights_basis": "cc_license",
            "rights_evidence_url": "https://example.com/rights",
            "license_url": "https://example.com/license",
        },
        {
            "default_workflow": "v3",
            "default_target_seconds": 15,
            "default_v3_platform": "youtube_shorts",
            "default_v3_audience": "short-form viewers",
            "default_v3_bpm": 120,
        },
        allow_skip_qc=False,
    )
    assert params["source_metadata"]["license_url"] == "https://example.com/license"


def test_idempotency_ignores_volatile_rights_timestamp(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    first = tmp_path / "video.mp4"
    first.write_bytes(b"same-video")
    base = {
        "topic": "same",
        "workflow": "v3",
        "_principal": "alice",
        "source_metadata": {
            "rights_status": "owned",
            "declared_at": "2026-10-01T15:00:00Z",
        },
    }
    later = {
        **base,
        "source_metadata": {
            **base["source_metadata"],
            "declared_at": "2026-10-01T15:05:00Z",
        },
    }
    assert appmod._request_fingerprint(base, first) == appmod._request_fingerprint(later, first)
