import importlib
import multiprocessing
import threading
import time

import pytest


def _load_dashboard(monkeypatch, tmp_path):
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIVF_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AIVF_OUTPUT_DIR", str(tmp_path / "output"))
    import web_app_v2
    importlib.reload(web_app_v2)
    return web_app_v2


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
    assert "job-crash" not in appmod._runtime_secrets


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
