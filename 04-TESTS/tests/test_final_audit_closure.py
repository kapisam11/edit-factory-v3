import json

from ai_video_factory.backup_restore import create_backup, restore_backup, verify_backup
from ai_video_factory.production_guardrails import redact_log_message
from ai_video_factory.job_class import JobClass, classify_job_class, budget_for
from dashboard_store import DashboardStore


def test_resource_classification_and_budget() -> None:
    assert classify_job_class({"enable_ocr": True}) == JobClass.AI_HEAVY.value
    assert classify_job_class({"gpu_render": True}) == JobClass.GPU_RENDER.value
    assert classify_job_class({"workflow": "package_only"}) == JobClass.LIGHT_QC.value
    assert budget_for(JobClass.CPU_RENDER.value).max_active == 2


def test_class_specific_reservation(tmp_path) -> None:
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id,topic,status,step,params,principal) VALUES('j','t','queued','waiting','{}','p')"
        )
    assert store.reserve_resources(
        "j",
        input_bytes=1,
        reserved_bytes=1,
        cpu_weight=1,
        memory_bytes=1,
        max_reserved_disk_bytes=100,
        max_cpu_weight=10,
        max_memory_bytes=100,
        resource_class=JobClass.GPU_RENDER.value,
    )
    with store.connect() as conn:
        row = conn.execute("SELECT resource_class FROM resource_reservations WHERE job_id='j'").fetchone()
    assert row["resource_class"] == JobClass.GPU_RENDER.value


def test_log_redaction_covers_urls_and_query_tokens() -> None:
    message = "https://user:password@example.test/a?access_token=secret123"
    redacted = redact_log_message(message)
    assert "password" not in redacted
    assert "secret123" not in redacted


def test_backup_restore_round_trip(tmp_path) -> None:
    db = tmp_path / "state.db"
    store = DashboardStore(db)
    store.ensure_indexes()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id,topic,status,step,params,principal) VALUES('j','t','done','done',?, 'p')",
            (json.dumps({"x": 1}),),
        )
    knowledge = tmp_path / "knowledge"
    output = tmp_path / "output"
    knowledge.mkdir()
    output.mkdir()
    (knowledge / "k.txt").write_text("knowledge", encoding="utf-8")
    (output / "final.mp4").write_bytes(b"fixture")
    archive = tmp_path / "backup.tar.gz"
    create_backup(db_path=db, backup_path=archive, knowledge_root=knowledge, output_root=output)
    assert verify_backup(archive)["ok"] is True
    destination = tmp_path / "restore"
    restore_backup(archive, destination)
    assert (destination / "jobs.db").is_file()
    assert (destination / "knowledge_base_v3" / "k.txt").read_text(encoding="utf-8") == "knowledge"
    assert (destination / "output" / "final.mp4").read_bytes() == b"fixture"
