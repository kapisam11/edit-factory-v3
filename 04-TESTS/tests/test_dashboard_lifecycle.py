import importlib
import multiprocessing
import threading
import time

import pytest


def _load_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIVF_OUTPUT_DIR", str(tmp_path / "output"))
    import web_app_v3
    importlib.reload(web_app_v3)
    return web_app_v3


def test_sqlite_concurrent_log_writes(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    appmod.db_insert_job("job-test", "topic", {"topic": "topic"})
    failures = []

    def writer(worker_id):
        try:
            for i in range(50):
                appmod.db_append_log("job-test", "INFO", f"{worker_id}:{i}")
        except Exception as exc:
            failures.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    assert len(appmod.db_logs_since("job-test")) == 400


def test_sqlite_write_retries_transient_lock(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    real_connect = appmod.sqlite3.connect
    attempts = {"count": 0}

    def flaky_connect(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise appmod.sqlite3.OperationalError("database is locked")
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(appmod.sqlite3, "connect", flaky_connect)
    monkeypatch.setattr(appmod.time, "sleep", lambda _delay: None)

    appmod.db_insert_job("job-retry", "topic", {"topic": "topic"})

    assert attempts["count"] == 3
    assert appmod.db_get_job("job-retry")["status"] == "queued"


def test_sqlite_write_does_not_retry_non_lock_error(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    attempts = {"count": 0}

    def failing_connect(*args, **kwargs):
        attempts["count"] += 1
        raise appmod.sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(appmod.sqlite3, "connect", failing_connect)
    monkeypatch.setattr(appmod.time, "sleep", lambda _delay: None)

    with pytest.raises(appmod.sqlite3.OperationalError, match="disk I/O error"):
        appmod.db_insert_job("job-io-error", "topic", {"topic": "topic"})

    assert attempts["count"] == 1


def test_retry_rejects_corrupted_persisted_parameters_without_queueing(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    from dashboard_optimizations import install_dashboard_optimizations

    install_dashboard_optimizations(appmod)
    appmod.db_insert_job("job-bad-params", "topic", {"topic": "topic"})
    appmod.db_update_job(
        "job-bad-params",
        status="error",
        step="failed",
        error="previous failure",
        params="{broken-json",
    )

    with appmod.app.test_request_context("/api/jobs/job-bad-params/retry", method="POST"):
        response, status = appmod.app.view_functions["retry_job"]("job-bad-params")

    assert status == 409
    assert response.get_json()["error"] == "Job parameters are invalid"
    job = appmod.db_get_job("job-bad-params")
    assert job["status"] == "error"
    assert job["step"] == "failed"
    assert job["error"] == "previous failure"



def test_retry_reports_worker_start_failure(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    from dashboard_optimizations import install_dashboard_optimizations

    install_dashboard_optimizations(appmod)
    assert "retry_job" in appmod.app.view_functions
    appmod.db_insert_job("job-start-failure", "topic", {"topic": "topic"})
    appmod.db_update_job("job-start-failure", status="error", step="failed", error="previous failure")

    def failing_start(job_id, params, secrets):
        appmod.db_update_job(
            job_id,
            status="error",
            step="failed",
            error="Worker failed to start: boom",
        )
        return False

    monkeypatch.setattr(appmod, "_start_job", failing_start)

    with appmod.app.test_request_context("/api/jobs/job-start-failure/retry", method="POST"):
        response, status = appmod.app.view_functions["retry_job"]("job-start-failure")

    payload = response.get_json()
    assert status == 500
    assert payload["job_id"] == "job-start-failure"
    assert payload["status"] == "error"
    assert payload["error"] == "Worker failed to start: boom"
    assert appmod.db_get_job("job-start-failure")["status"] == "error"

def test_job_redaction_hides_internal_paths_and_principal(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    appmod.db_insert_job(
        "job-redact",
        "topic",
        {
            "topic": "topic",
            "raw_video": str(tmp_path / "uploads" / "source.mp4"),
            "_principal": "127.0.0.1",
            "_retry_secret_keys": ["model_key"],
            "workflow": "v3",
            "target_seconds": 8,
        },
    )
    appmod.db_update_job("job-redact", pkg_dir=str(tmp_path / "output" / "job-redact"))
    payload = appmod._redact_job(appmod.db_get_job("job-redact"))
    assert "pkg_dir" not in payload
    assert payload["package_name"] == "job-redact"
    assert payload["params"]["workflow"] == "v3"
    assert "raw_video" not in payload["params"]
    assert "_principal" not in payload["params"]
    assert "_retry_secret_keys" not in payload["params"]


def test_health_reports_runtime_capability_contract(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    response = appmod.app.test_client().get("/api/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert "max_concurrent_jobs" in payload
    assert set(payload["capabilities"]) == {"ocr", "object_detection", "diarization"}


def test_retry_preserves_retained_one_off_secret(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    from dashboard_optimizations import install_dashboard_optimizations
    install_dashboard_optimizations(appmod)
    appmod.db_insert_job(
        "job-secret-retry",
        "topic",
        {"topic": "topic", "workflow": "default", "_retry_secret_keys": ["model_key"]},
    )
    appmod.db_update_job("job-secret-retry", status="error", step="failed", error="previous failure")

    captured = {}
    appmod._runtime_secrets["job-secret-retry"] = {"model_key": "one-off-secret"}

    def fake_start(job_id, params, secrets):
        captured.update(secrets)
        appmod.db_update_job(job_id, status="running", step="started")
        return True

    monkeypatch.setattr(appmod, "_start_job", fake_start)
    with appmod.app.test_request_context("/api/jobs/job-secret-retry/retry", method="POST"):
        response, status = appmod.app.view_functions["retry_job"]("job-secret-retry")

    assert status == 202
    assert captured["model_key"] == "one-off-secret"


def test_startup_reconciles_non_terminal_jobs(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    appmod.db_insert_job("queued", "queued", {"topic": "queued"})
    appmod.db_insert_job("running", "running", {"topic": "running"})
    appmod.db_update_job("running", status="running")
    importlib.reload(appmod)
    assert appmod.db_get_job("queued")["status"] == "interrupted"
    assert appmod.db_get_job("running")["status"] == "interrupted"


def test_real_worker_process_can_be_terminated(tmp_path):
    from dashboard_compat import _terminate_process_tree
    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(target=time.sleep, args=(60,), daemon=False)
    process.start()
    try:
        assert process.is_alive()
        _terminate_process_tree(process)
        assert not process.is_alive()
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)


def test_cancellation_does_not_overwrite_terminal_job(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    appmod.db_insert_job("job-done", "topic", {"topic": "topic"})
    appmod.db_update_job("job-done", status="done", step="Complete")

    from dashboard_compat import cancel_process

    with appmod.app.test_request_context("/api/jobs/job-done/cancel", method="POST"):
        response, status = cancel_process("job-done")

    assert status == 409
    assert response.get_json()["status"] == "done"
    assert appmod.db_get_job("job-done")["status"] == "done"


def test_worker_exit_reconciles_running_job_and_releases_secrets(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    appmod.db_insert_job("job-crash", "topic", {"topic": "topic"})
    appmod.db_update_job("job-crash", status="running", step="Rendering")
    appmod._runtime_secrets["job-crash"] = {"model_key": "secret"}

    class DeadProcess:
        exitcode = 1

        def join(self, *args, **kwargs):
            return None

    from dashboard_compat import _watch_job_process

    _watch_job_process(appmod, "job-crash", DeadProcess())

    job = appmod.db_get_job("job-crash")
    assert job["status"] == "interrupted"
    assert "Worker exited unexpectedly" in job["error"]
    assert "job-crash" in appmod._runtime_secrets
    assert "job-crash" in appmod._runtime_secret_expiry


def test_worker_exit_after_cancellation_is_terminal(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    appmod.db_insert_job("job-cancel", "topic", {"topic": "topic"})
    appmod.db_update_job("job-cancel", status="cancelling", step="cancelling")

    class DeadProcess:
        exitcode = -15

        def join(self, *args, **kwargs):
            return None

    from dashboard_compat import _watch_job_process

    _watch_job_process(appmod, "job-cancel", DeadProcess())

    assert appmod.db_get_job("job-cancel")["status"] == "cancelled"


def test_v3_preview_serves_canonical_artifact(monkeypatch, tmp_path):
    appmod = _load_dashboard(monkeypatch, tmp_path)
    from dashboard_optimizations import install_dashboard_optimizations
    import ai_video_factory.artifact_readiness as artifact_readiness

    install_dashboard_optimizations(appmod)
    package = tmp_path / "output" / "job-preview"
    package.mkdir(parents=True)
    final_video = package / "final.v3.mp4"
    final_video.write_bytes(b"fake-mp4-bytes")
    appmod.db_insert_job("job-preview", "preview", {"topic": "preview", "workflow": "v3"})
    appmod.db_update_job("job-preview", status="done", step="Complete (V3)", pkg_dir=str(package))

    monkeypatch.setattr(artifact_readiness, "resolve_final_video", lambda _package: final_video)

    response = appmod.app.test_client().get("/api/jobs/job-preview/preview")
    assert response.status_code == 200
    assert response.mimetype == "video/mp4"
    assert response.data == b"fake-mp4-bytes"
