import sys
from types import ModuleType, SimpleNamespace

import pytest

import dashboard_worker

from dashboard_worker import _skip_stages_for_workflow


def test_dashboard_workflows_map_to_expected_pipeline_stages():
    assert _skip_stages_for_workflow("default") == []
    assert _skip_stages_for_workflow("director") == []
    assert _skip_stages_for_workflow("fast") == ["research", "thumbnail", "voiceover", "music", "quality_control", "metrics"]
    assert _skip_stages_for_workflow("package_only") == ["auto_edit", "voiceover", "music", "quality_control", "metrics"]
    assert _skip_stages_for_workflow("v3") == []


def test_dashboard_workflow_rejects_unknown_value():
    with pytest.raises(ValueError):
        _skip_stages_for_workflow("not-a-workflow")


def test_run_job_executes_worker_and_cleans_up(monkeypatch):
    calls = {}

    class FakeThread:
        def __init__(self, target, name, daemon):
            calls["thread"] = (target, name, daemon)

        def start(self):
            calls["started"] = True

        def join(self, timeout):
            calls["joined_timeout"] = timeout

    class FakeStore:
        def __init__(self, db_path):
            calls["store_db_path"] = db_path

        def begin_attempt(self, job_id, worker_id, workspace):
            calls["attempt"] = (job_id, worker_id, workspace)
            return {
                "attempt_id": "attempt-1",
                "lease_token": "lease-1",
                "worker_id": worker_id,
                "attempt_number": "1",
            }

        def heartbeat_attempt(self, job_id, attempt_id, lease_token):
            return True

        def finish_attempt(self, job_id, attempt_id, lease_token, **kwargs):
            calls["finished"] = (job_id, attempt_id, lease_token, kwargs)
            return True

        def get_job(self, job_id):
            return {"status": "done"}

        def release_resources(self, job_id):
            calls["released"] = job_id
            return True

    def fake_run_job_worker_impl(job_id, params, secrets, output_root, db_path, skip_stages):
        calls["worker"] = (job_id, params.copy(), secrets, output_root, db_path, skip_stages)

    app_module = ModuleType("app")
    app_module.web_app_v3 = SimpleNamespace(_run_job_worker_impl=fake_run_job_worker_impl)
    store_module = ModuleType("dashboard_store")
    store_module.DashboardStore = FakeStore

    monkeypatch.setitem(sys.modules, "app", app_module)
    monkeypatch.setitem(sys.modules, "dashboard_store", store_module)
    monkeypatch.setattr(dashboard_worker.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        dashboard_worker.os,
        "setsid",
        lambda: calls.__setitem__("setsid", True),
        raising=False,
    )
    monkeypatch.setattr(
        "ai_video_factory.validation.validate_target_seconds",
        lambda value: 60.0,
    )

    dashboard_worker.run_job(
        "job-123",
        {"workflow": "fast", "target_seconds": 22},
        {"API_KEY": "secret"},
        "/tmp/output",
        "/tmp/jobs.db",
    )

    assert calls["store_db_path"] == "/tmp/jobs.db"
    assert calls["started"] is True
    assert calls["joined_timeout"] == 5.0
    assert calls["worker"] == (
        "job-123",
        {
            "workflow": "fast",
            "target_seconds": 60.0,
            "_attempt_id": "attempt-1",
            "_lease_token": "lease-1",
            "_worker_id": calls["attempt"][1],
            "_workspace_root": "/tmp/output/.work/job-123/attempt-1",
        },
        {"API_KEY": "secret"},
        "/tmp/output",
        "/tmp/jobs.db",
        ["research", "thumbnail", "voiceover", "music", "quality_control", "metrics"],
    )


def test_run_job_marks_invalid_heartbeat_interval_as_error(monkeypatch):
    calls = {}

    class FakeThread:
        def __init__(self, *args, **kwargs):
            raise AssertionError("worker thread must not start for an invalid interval")

    class FakeStore:
        def __init__(self, db_path):
            calls["db_path"] = db_path

        def update_job_if_status(self, job_id, statuses, **kwargs):
            calls["update"] = (job_id, statuses, kwargs)

    store_module = ModuleType("dashboard_store")
    store_module.DashboardStore = FakeStore
    monkeypatch.setitem(sys.modules, "dashboard_store", store_module)
    monkeypatch.setattr(dashboard_worker.threading, "Thread", FakeThread)
    monkeypatch.setenv("AIVF_WORKER_HEARTBEAT_SECONDS", "not-a-number")

    dashboard_worker.run_job(
        "job-456",
        {"workflow": "default", "target_seconds": 30},
        {},
        "/tmp/output",
        "/tmp/jobs.db",
    )

    assert calls["db_path"] == "/tmp/jobs.db"
    assert calls["update"] == (
        "job-456",
        ("queued", "running"),
        {
            "status": "error",
            "step": "failed",
            "error": "AIVF_WORKER_HEARTBEAT_SECONDS must be numeric and greater than 0",
        },
    )
