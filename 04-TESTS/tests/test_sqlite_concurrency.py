import threading

from web_app_v2 import db_append_log, db_get_job, db_insert_job, get_db


def test_concurrent_log_writes_are_append_only(tmp_path, monkeypatch):
    monkeypatch.setenv("AIVF_STATE_DIR", str(tmp_path / "state"))
    import importlib
    import web_app_v2
    importlib.reload(web_app_v2)
    job_id = "job_concurrency"
    web_app_v2.db_insert_job(job_id, "concurrency", {"topic": "concurrency"})

    errors = []

    def write(i):
        try:
            web_app_v2.db_append_log(job_id, "INFO", f"message-{i}")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    logs = web_app_v2.db_logs_since(job_id)
    assert len(logs) == 20
