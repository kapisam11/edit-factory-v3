import importlib
import io
import json


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIVF_OUTPUT_DIR", str(tmp_path / "output"))
    import web_app_v2
    importlib.reload(web_app_v2)
    web_app_v2.app.config["TESTING"] = True
    return web_app_v2


def test_job_params_never_persist_secret_keys(monkeypatch, tmp_path):
    appmod = _app(monkeypatch, tmp_path)
    appmod._start_job = lambda *args, **kwargs: True
    client = appmod.app.test_client()

    response = client.post("/api/jobs", json={
        "topic": "safe topic",
        "model_key": "secret-model-key",
        "groq_key": "secret-groq-key",
    })
    assert response.status_code == 202
    job_id = response.get_json()["job_id"]

    job = appmod.db_get_job(job_id)
    raw_params = json.loads(job["params"])
    assert "model_key" not in raw_params
    assert "groq_key" not in raw_params


def test_package_traversal_is_rejected(monkeypatch, tmp_path):
    appmod = _app(monkeypatch, tmp_path)
    client = appmod.app.test_client()
    response = client.get("/api/packages/../../etc/file/passwd")
    assert response.status_code in {404, 308}


def test_invalid_video_upload_is_rejected(monkeypatch, tmp_path):
    appmod = _app(monkeypatch, tmp_path)
    appmod._probe_video = lambda path: False
    client = appmod.app.test_client()
    response = client.post(
        "/api/jobs",
        data={
            "topic": "bad upload",
            "raw_video": (io.BytesIO(b"not video"), "clip.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
