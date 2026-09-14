from types import SimpleNamespace

import dashboard_shutdown


def test_shutdown_imports_canonical_dashboard_module(monkeypatch):
    active = {"job": SimpleNamespace(status="running")}
    web = SimpleNamespace(
        _active_processes=active,
        _runtime_secrets={"job": {"model_key": "secret"}},
        TERMINAL_STATUSES={"done", "error", "cancelled", "interrupted"},
        db_get_job=lambda job_id: {"status": "running"},
        db_update_job=lambda *args, **kwargs: None,
        db_append_log=lambda *args, **kwargs: None,
    )
    process = active["job"]
    calls = []

    class Compat:
        @staticmethod
        def _terminate_process_tree(process):
            calls.append(process)

    def fake_import(name):
        if name == "app.web_app_v3":
            return web
        if name == "dashboard_compat":
            return Compat
        raise AssertionError(name)

    monkeypatch.setattr(dashboard_shutdown, "import_module", fake_import)
    dashboard_shutdown.shutdown_active_workers()

    assert calls == [process]
    assert active == {}
    assert web._runtime_secrets == {}
