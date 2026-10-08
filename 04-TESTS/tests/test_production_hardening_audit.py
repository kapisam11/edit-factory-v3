import threading
import pytest
from ai_video_factory.asset_manager import RUNTIME_ASSETS, verify_runtime_asset
from ai_video_factory.media_limits import MediaLimits, probe_media_contract
from ai_video_factory.retention_janitor import run_janitor
from dashboard_store import DashboardStore

def test_resource_reservation_is_atomic(tmp_path):
    store=DashboardStore(tmp_path/"jobs.db"); store.ensure_indexes()
    with store.connect() as conn:
        for jid in ("j1","j2"):
            conn.execute("INSERT INTO jobs(id,topic,status,step,params,principal) VALUES(?,?,?,?,?,?)",(jid,"t","queued","waiting","{}","p"))
    results=[]
    def reserve(jid):
        results.append(store.reserve_resources(jid,input_bytes=1,reserved_bytes=100,cpu_weight=1,memory_bytes=100,max_reserved_disk_bytes=150,max_cpu_weight=2,max_memory_bytes=150))
    threads=[threading.Thread(target=reserve,args=(jid,)) for jid in ("j1","j2")]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert sum(bool(x) for x in results)==1

def test_attempt_fencing_blocks_stale_publish(tmp_path):
    store=DashboardStore(tmp_path/"jobs.db"); store.ensure_indexes()
    with store.connect() as conn:
        conn.execute("INSERT INTO jobs(id,topic,status,step,params,principal) VALUES('j','t','running','starting','{}','p')")
    first=store.begin_attempt("j","worker-a",str(tmp_path/".work/j")); assert first
    assert store.finish_attempt("j",first["attempt_id"],first["lease_token"],status="failed")
    with store.connect() as conn: conn.execute("UPDATE jobs SET status='running' WHERE id='j'")
    second=store.begin_attempt("j","worker-b",str(tmp_path/".work/j")); assert second
    assert not store.attempt_can_publish("j",first["attempt_id"],first["lease_token"])
    assert store.attempt_can_publish("j",second["attempt_id"],second["lease_token"])

def test_runtime_asset_requires_hash(tmp_path):
    spec=RUNTIME_ASSETS[0]; path=tmp_path/spec.relative_path; path.parent.mkdir(parents=True); path.write_bytes(b"fixture")
    assert verify_runtime_asset(spec,tmp_path) is False

def test_media_rejects_extension_only_fake(tmp_path):
    path=tmp_path/"fake.mp4"; path.write_bytes(b"not-an-mp4")
    with pytest.raises(ValueError,match="container signature"): probe_media_contract(path,MediaLimits(max_input_bytes=1024))

def test_janitor_never_removes_active_workspace(tmp_path):
    db=tmp_path/"jobs.db"; store=DashboardStore(db); store.ensure_indexes()
    workspace=tmp_path/"output/.work/j/attempt"; workspace.mkdir(parents=True); (workspace/"keep.bin").write_bytes(b"x")
    with store.connect() as conn:
        conn.execute("INSERT INTO jobs(id,topic,status,step,params,principal) VALUES('j','t','running','running','{}','p')")
        conn.execute("INSERT INTO job_attempts(id,job_id,attempt_number,lease_token,workspace,status) VALUES('a','j',1,'token',?,'running')",(str(workspace),))
    result=run_janitor(db_path=db,upload_root=tmp_path/"uploads",output_root=tmp_path/"output",workspace_root=tmp_path/"output/.work",now=90*86400,failed_workspace_hours=1)
    assert result["failed_workspaces"]==0 and (workspace/"keep.bin").exists()

def test_janitor_prunes_terminal_db_state(tmp_path):
    db=tmp_path/"jobs.db"; store=DashboardStore(db); store.ensure_indexes()
    with store.connect() as conn:
        conn.execute("INSERT INTO jobs(id,topic,status,step,params,principal,created_at,updated_at) VALUES('old','t','done','done','{}','p','1970-01-01','1970-01-01')")
        conn.execute("INSERT INTO job_logs(job_id,level,message,created_at) VALUES('old','INFO','x','1970-01-01')")
    result=run_janitor(db_path=db,upload_root=tmp_path/"uploads",output_root=tmp_path/"output",now=90*86400,prune_terminal_jobs=True,metadata_days=30)
    assert result["terminal_jobs"]==1
    with store.connect() as conn: assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]==0


def test_cancelled_attempt_lease_is_revoked(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()
    store.insert_job("job-cancel", "topic", {}, principal="owner")
    assert store.claim_job("job-cancel")
    attempt = store.begin_attempt("job-cancel", "worker-a", str(tmp_path / "work"))
    assert attempt
    with store.connect() as conn:
        conn.execute("UPDATE jobs SET status='cancelling' WHERE id='job-cancel'")
    assert store.revoke_attempt_lease("job-cancel")
    assert not store.attempt_can_publish(
        "job-cancel", attempt["attempt_id"], attempt["lease_token"]
    )
    job = store.get_job("job-cancel")
    assert job["lease_token"] is None


def test_fenced_publish_commits_done_and_artifact_atomically(tmp_path):
    store = DashboardStore(tmp_path / "jobs.db")
    store.ensure_indexes()
    store.insert_job("job-publish", "topic", {}, principal="owner")
    assert store.claim_job("job-publish")
    attempt = store.begin_attempt("job-publish", "worker-a", str(tmp_path / "work"))
    assert attempt
    package = tmp_path / "output" / "package"
    artifact = package / "final.mp4"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"fixture")

    assert store.finalize_attempt_publish(
        "job-publish",
        attempt["attempt_id"],
        attempt["lease_token"],
        package_path=package,
        artifact_path=artifact,
        artifact_sha256="a" * 64,
        artifact_size_bytes=artifact.stat().st_size,
    )
    job = store.get_job("job-publish")
    assert job["status"] == "done"
    assert str(job["pkg_dir"]) == str(package.resolve())
    with store.connect() as conn:
        row = conn.execute(
            "SELECT kind, attempt_id, path, sha256, size_bytes FROM job_artifacts WHERE job_id=?",
            ("job-publish",),
        ).fetchone()
    assert row["kind"] == "final_video"
    assert row["attempt_id"] == attempt["attempt_id"]
    assert row["sha256"] == "a" * 64


def test_production_claim_refuses_runtime_schema_mutation(monkeypatch, tmp_path):
    db = tmp_path / "legacy.db"
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE jobs(id TEXT PRIMARY KEY, topic TEXT, status TEXT, step TEXT, params TEXT)"
        )
        conn.execute(
            "INSERT INTO jobs(id,topic,status,step,params) VALUES('legacy','t','queued','waiting','{}')"
        )

    monkeypatch.setenv("AIVF_ENV", "production")
    monkeypatch.delenv("AIVF_ALLOW_RUNTIME_MIGRATIONS", raising=False)
    store = DashboardStore(db)
    with pytest.raises(Exception, match="production database schema is missing lifecycle columns"):
        store.claim_job("legacy")

    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert "lease_token" not in columns
    assert "principal" not in columns


def test_production_janitor_prunes_terminal_metadata_by_default(monkeypatch, tmp_path):
    db = tmp_path / "jobs.db"
    store = DashboardStore(db)
    store.ensure_indexes()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id,topic,status,step,params,principal,created_at,updated_at) "
            "VALUES('old','t','done','done','{}','p','1970-01-01','1970-01-01')"
        )

    monkeypatch.setenv("AIVF_ENV", "production")
    monkeypatch.delenv("AIVF_PRUNE_TERMINAL_JOBS", raising=False)
    result = run_janitor(
        db_path=db,
        upload_root=tmp_path / "uploads",
        output_root=tmp_path / "output",
        now=90 * 86400,
        metadata_days=30,
        prune_terminal_jobs=None,
    )
    assert result["terminal_jobs"] == 1
    assert result["analyzed"] == 1
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
