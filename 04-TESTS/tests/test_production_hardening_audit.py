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
