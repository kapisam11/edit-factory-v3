import sqlite3

from ai_video_factory.backup_restore import create_backup, restore_backup, verify_backup


def test_backup_restore_roundtrip(tmp_path):
    state = tmp_path / "state"
    knowledge = tmp_path / "knowledge"
    output = tmp_path / "output"
    backups = tmp_path / "backups"
    restored_state = tmp_path / "restored-state"
    restored_knowledge = tmp_path / "restored-knowledge"
    restored_output = tmp_path / "restored-output"
    for path in (state, knowledge, output):
        path.mkdir(parents=True)

    db = state / "jobs.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, topic TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('job-1', 'backup test')")
    conn.commit()
    conn.close()
    (knowledge / "kb.txt").write_text("knowledge", encoding="utf-8")
    (output / "artifact.txt").write_text("artifact", encoding="utf-8")

    created = create_backup(
        state_dir=state,
        knowledge_dir=knowledge,
        output_dir=output,
        destination=backups,
    )
    assert verify_backup(created["path"])["ok"]

    restored = restore_backup(
        backup_dir=created["path"],
        state_dir=restored_state,
        knowledge_dir=restored_knowledge,
        output_dir=restored_output,
    )
    assert restored["ok"]

    conn = sqlite3.connect(restored_state / "jobs.db")
    try:
        assert conn.execute("SELECT topic FROM jobs WHERE id='job-1'").fetchone()[0] == "backup test"
    finally:
        conn.close()
    assert (restored_knowledge / "kb.txt").read_text(encoding="utf-8") == "knowledge"
    assert (restored_output / "artifact.txt").read_text(encoding="utf-8") == "artifact"
