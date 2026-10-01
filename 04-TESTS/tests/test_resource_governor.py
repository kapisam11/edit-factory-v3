import json
import sqlite3

import pytest

from resource_governor import (
    ResourceLimitExceeded,
    acquire_sse,
    check_job_creation_limits,
    directory_size,
    release_sse,
)


def test_principal_queue_limit(monkeypatch, tmp_path):
    db_path = tmp_path / "jobs.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE jobs(params TEXT, status TEXT)")
        for _ in range(2):
            conn.execute(
                "INSERT INTO jobs(params,status) VALUES(?, 'queued')",
                (json.dumps({"_principal": "same-client"}),),
            )
    monkeypatch.setattr("resource_governor.MAX_QUEUED_PER_PRINCIPAL", 2)
    with pytest.raises(ResourceLimitExceeded, match="principal queue capacity"):
        check_job_creation_limits(
            lambda: sqlite3.connect(db_path),
            "same-client",
            (tmp_path,),
        )


def test_storage_quota_rejects_before_creation(monkeypatch, tmp_path):
    marker = tmp_path / "large.bin"
    marker.write_bytes(b"x" * 32)
    db_path = tmp_path / "jobs.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE jobs(params TEXT, status TEXT)")
    monkeypatch.setattr("resource_governor.MAX_TOTAL_STORAGE_BYTES", 16)
    with pytest.raises(ResourceLimitExceeded, match="total storage quota"):
        check_job_creation_limits(lambda: sqlite3.connect(db_path), "client", (tmp_path,))


def test_sse_counter_is_bounded():
    import resource_governor as governor
    original = governor.MAX_SSE_CONNECTIONS
    governor.MAX_SSE_CONNECTIONS = 2
    try:
        assert acquire_sse() is True
        assert acquire_sse() is True
        assert acquire_sse() is False
        release_sse()
        assert acquire_sse() is True
    finally:
        release_sse()
        release_sse()
        governor.MAX_SSE_CONNECTIONS = original



def test_resource_governor_validation_and_request_principals(monkeypatch):
    import flask
    from types import SimpleNamespace
    import resource_governor as governor

    monkeypatch.setenv("AIVF_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("TEST_INTEGER", "not-an-integer")
    with pytest.raises(ValueError, match="must be an integer"):
        governor._int_env("TEST_INTEGER", 2)
    monkeypatch.setenv("TEST_INTEGER", "0")
    with pytest.raises(ValueError, match="must be >= 1"):
        governor._int_env("TEST_INTEGER", 2)

    app = flask.Flask(__name__)
    app.secret_key = "test-secret"
    with app.test_request_context(
        "/",
        environ_overrides={
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_X_FORWARDED_FOR": "203.0.113.10, 10.0.0.1",
        },
    ):
        flask.session["aivf_authenticated"] = True
        first = governor.principal_for_request(flask.request)
        second = governor.principal_for_request(flask.request)
        assert first.startswith("session:")
        assert first == second

    fallback_request = SimpleNamespace(
        remote_addr="127.0.0.1",
        headers={"X-Forwarded-For": "203.0.113.10, 10.0.0.1"},
    )
    assert governor.principal_for_request(fallback_request) == "203.0.113.10"

    def explode():
        raise RuntimeError("no request context")

    monkeypatch.setattr(flask, "has_request_context", explode)
    assert governor.principal_for_request(SimpleNamespace(remote_addr="198.51.100.7", headers={})) == "198.51.100.7"


def test_resource_governor_directory_and_storage_error_paths(monkeypatch, tmp_path):
    import resource_governor as governor
    from resource_governor import job_storage_ok

    def broken_rglob(_self, _pattern):
        raise OSError("simulated rglob failure")

    monkeypatch.setattr(type(tmp_path), "rglob", broken_rglob)
    assert governor.directory_size(tmp_path) == 0

    class BrokenEntry:
        def is_file(self):
            raise OSError("simulated stat failure")

    monkeypatch.setattr(type(tmp_path), "rglob", lambda _self, _pattern: [BrokenEntry()])
    assert governor.directory_size(tmp_path) == 0
    assert job_storage_ok(tmp_path) is True


def test_principal_queued_jobs_ignores_malformed_params(tmp_path):
    import resource_governor as governor

    db_path = tmp_path / "jobs.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE jobs(params TEXT, status TEXT)")
        conn.execute(
            "INSERT INTO jobs(params,status) VALUES(?, 'queued')",
            ("not-json",),
        )
        conn.execute(
            "INSERT INTO jobs(params,status) VALUES(?, 'queued')",
            (json.dumps({"_principal": "valid"}),),
        )

    assert governor.principal_queued_jobs(
        lambda: sqlite3.connect(db_path),
        "valid",
    ) == 1


def test_directory_size_does_not_follow_symlinks(tmp_path):
    from resource_governor import directory_size

    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x" * 100)
    root = tmp_path / "root"
    root.mkdir()
    (root / "inside.bin").write_bytes(b"y" * 3)
    try:
        (root / "outside-link").symlink_to(outside)
        (root / "dir-link").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    assert directory_size(root) == 3
