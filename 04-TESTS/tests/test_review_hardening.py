import json
from pathlib import Path

import pytest

from ai_video_factory.backup_restore import create_backup, verify_backup
from ai_video_factory.db_migrations import CURRENT_SCHEMA_VERSION, migrate_database, verify_database_schema
from ai_video_factory.media_limits import MediaLimits, estimate_resource_budget, probe_media_contract
from ai_video_factory.release_check import check_release
from dashboard_store import DashboardStore


def test_media_contract_rejects_fake_container_before_ffprobe(monkeypatch, tmp_path):
    target = tmp_path / "fake.mp4"
    target.write_bytes(b"not-an-mp4")
    called = {"ffprobe": False}

    def fail_if_called(*args, **kwargs):
        called["ffprobe"] = True
        raise AssertionError("ffprobe must not run for an invalid container signature")

    monkeypatch.setattr("ai_video_factory.media_limits.run_ffprobe", fail_if_called)
    with pytest.raises(ValueError, match="container signature"):
        probe_media_contract(target, MediaLimits(max_input_bytes=1024))
    assert called["ffprobe"] is False


def test_resource_budget_is_bounded():
    limits = MediaLimits(max_output_bytes=1024**3)
    budget = estimate_resource_budget(
        {
            "size_bytes": 100 * 1024**2,
            "width": 3840,
            "height": 2160,
            "fps": 60,
        },
        target_seconds=180,
        limits=limits,
    )
    assert budget["reserved_bytes"] <= limits.max_output_bytes
    assert 0.5 <= budget["cpu_weight"] <= 4.0
    assert 3 * 1024**3 <= budget["memory_bytes"] <= 12 * 1024**3


def test_resource_reservations_never_exceed_capacity(tmp_path):
    store = DashboardStore(str(tmp_path / "jobs.db"))
    store.ensure_indexes()
    assert store.reserve_resources(
        "job-1",
        input_bytes=1,
        reserved_bytes=800,
        cpu_weight=4,
        memory_bytes=800,
        max_reserved_disk_bytes=1000,
        max_cpu_weight=5,
        max_memory_bytes=1000,
    )
    assert not store.reserve_resources(
        "job-2",
        input_bytes=1,
        reserved_bytes=400,
        cpu_weight=2,
        memory_bytes=300,
        max_reserved_disk_bytes=1000,
        max_cpu_weight=5,
        max_memory_bytes=1000,
    )
    assert store.release_resources("job-1")
    assert store.reserve_resources(
        "job-2",
        input_bytes=1,
        reserved_bytes=400,
        cpu_weight=2,
        memory_bytes=300,
        max_reserved_disk_bytes=1000,
        max_cpu_weight=5,
        max_memory_bytes=1000,
    )


def test_stale_attempt_cannot_publish(tmp_path):
    store = DashboardStore(str(tmp_path / "jobs.db"))
    store.ensure_indexes()
    store.insert_job("job-1", "topic", {"_principal": "owner"}, principal="owner")
    assert store.claim_job("job-1")

    first = store.begin_attempt("job-1", "worker-a", str(tmp_path / "work"))
    assert first is not None
    assert store.attempt_can_publish(
        "job-1", first["attempt_id"], first["lease_token"]
    )

    second = store.begin_attempt("job-1", "worker-b", str(tmp_path / "work"))
    assert second is not None
    assert not store.attempt_can_publish(
        "job-1", first["attempt_id"], first["lease_token"]
    )
    assert store.attempt_can_publish(
        "job-1", second["attempt_id"], second["lease_token"]
    )


def test_database_migration_is_explicit_and_verifiable(tmp_path):
    db = tmp_path / "jobs.db"
    assert migrate_database(db) == CURRENT_SCHEMA_VERSION
    verify_database_schema(db)


def test_backup_round_trip_is_verified(tmp_path):
    db = tmp_path / "jobs.db"
    store = DashboardStore(str(db))
    store.ensure_indexes()
    store.insert_job("job-1", "topic", {"_principal": "owner"}, principal="owner")

    backup = tmp_path / "backup.tar.gz"
    create_backup(db_path=db, backup_path=backup)
    marker = tmp_path / "restore-verified"
    result = verify_backup(backup, marker_path=marker)

    assert result["ok"] is True
    assert result["verified_files"] >= 1
    assert marker.is_file()


def test_release_check_blocks_unproven_production(tmp_path, monkeypatch):
    monkeypatch.setenv("AIVF_CI_STATUS", "green")
    monkeypatch.setenv("AIVF_SECURITY_STATUS", "green")
    monkeypatch.setenv("AIVF_E2E_STATUS", "green")
    monkeypatch.setenv("AIVF_HUMAN_APPROVAL", "approved")
    monkeypatch.setenv("AIVF_IMAGE", "ghcr.io/test/app@sha256:" + "a" * 64)
    monkeypatch.setenv("AIVF_MOBILENET_CONFIG_SHA256", "b" * 64)
    monkeypatch.setenv("AIVF_MOBILENET_WEIGHTS_SHA256", "c" * 64)
    monkeypatch.setenv("AIVF_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setenv(
        "AIVF_BACKUP_RESTORE_MARKER",
        str(tmp_path / "missing-marker"),
    )
    result = check_release(tmp_path)
    assert result["ok"] is False
    assert "database_migrated" in result["failures"]
    assert "backup_restore_verified" in result["failures"]
