"""Production retention/janitor for durable job state and filesystem artifacts."""
from __future__ import annotations
import argparse, json, os, shutil, sqlite3, time
from pathlib import Path
from typing import Iterable
TERMINAL=("done","error","cancelled","interrupted")

def _int_env(name, default, minimum=1):
    try: value=int(os.environ.get(name,str(default)))
    except ValueError as exc: raise ValueError(f"{name} must be an integer") from exc
    if value<minimum: raise ValueError(f"{name} must be >= {minimum}")
    return value

def _connect(db):
    conn=sqlite3.connect(str(db),timeout=10)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn

def _cutoff_days(days, now): return now-float(days)*86400.0

def _safe_child(root, raw):
    if not raw: return None
    candidate=Path(raw).resolve(); root=Path(root).resolve()
    try: candidate.relative_to(root)
    except ValueError: return None
    return candidate

def run_janitor(*,db_path,upload_root,output_root,workspace_root=None,now=None,metadata_days=None,logs_days=None,events_days=None,idempotency_hours=None,upload_days=None,failed_workspace_hours=None,prune_terminal_jobs=None):
    """Prune only expired, unreferenced state; active jobs are never touched."""
    now=time.time() if now is None else float(now)
    metadata_days=metadata_days or _int_env("AIVF_JOB_METADATA_RETENTION_DAYS",30)
    logs_days=logs_days or _int_env("AIVF_LOG_RETENTION_DAYS",7)
    events_days=events_days or _int_env("AIVF_EVENT_RETENTION_DAYS",30)
    idempotency_hours=idempotency_hours or _int_env("AIVF_IDEMPOTENCY_RETENTION_HOURS",24)
    upload_days=upload_days or _int_env("AIVF_UPLOAD_RETENTION_DAYS",7)
    failed_workspace_hours=failed_workspace_hours or _int_env("AIVF_FAILED_WORKSPACE_RETENTION_HOURS",24)
    prune_terminal_jobs = (os.environ.get("AIVF_PRUNE_TERMINAL_JOBS","0")=="1") if prune_terminal_jobs is None else bool(prune_terminal_jobs)
    db=Path(db_path).resolve(); uploads=Path(upload_root).resolve(); output=Path(output_root).resolve(); work=Path(workspace_root or (output/".work")).resolve()
    counts={k:0 for k in ("logs","events","audit_events","idempotency_keys","rate_limits","uploads","failed_workspaces","terminal_jobs")}
    active=set()
    with _connect(db) as conn:
        try:
            rows=conn.execute("SELECT id FROM jobs WHERE status IN ('queued','running','cancelling')").fetchall()
            for row in rows:
                a=conn.execute("SELECT workspace FROM job_attempts WHERE job_id=? AND status='running' ORDER BY started_at DESC LIMIT 1",(row["id"],)).fetchone()
                if a:
                    p=_safe_child(work,a["workspace"])
                    if p: active.add(p)
        except sqlite3.Error:
            pass
        counts["logs"]=max(0,conn.execute("DELETE FROM job_logs WHERE created_at < datetime(?, 'unixepoch')",(_cutoff_days(logs_days,now),)).rowcount)
        counts["events"]=max(0,conn.execute("DELETE FROM job_events WHERE created_at < datetime(?, 'unixepoch')",(_cutoff_days(events_days,now),)).rowcount)
        counts["audit_events"]=max(0,conn.execute("DELETE FROM audit_events WHERE created_at < datetime(?, 'unixepoch')",(_cutoff_days(events_days,now),)).rowcount)
        counts["idempotency_keys"]=max(0,conn.execute("DELETE FROM idempotency_keys WHERE created_at < ?",(_cutoff_days(idempotency_hours/24.0,now),)).rowcount)
        counts["rate_limits"]=max(0,conn.execute("DELETE FROM rate_limits WHERE ts < ?",(_cutoff_days(1,now),)).rowcount)
        if prune_terminal_jobs:
            rows=conn.execute("SELECT id FROM jobs WHERE status IN ('done','error','cancelled','interrupted') AND updated_at < datetime(?, 'unixepoch')",(_cutoff_days(metadata_days,now),)).fetchall()
            for row in rows:
                jid=str(row["id"])
                conn.execute("DELETE FROM job_artifacts WHERE job_id=?",(jid,))
                conn.execute("DELETE FROM job_attempts WHERE job_id=?",(jid,))
                conn.execute("DELETE FROM job_events WHERE job_id=?",(jid,))
                conn.execute("DELETE FROM job_logs WHERE job_id=?",(jid,))
                conn.execute("DELETE FROM jobs WHERE id=? AND status IN ('done','error','cancelled','interrupted')",(jid,))
                counts["terminal_jobs"]+=1
    if uploads.is_dir():
        cutoff=_cutoff_days(upload_days,now)
        for path in uploads.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime<cutoff:
                    path.unlink(missing_ok=True); counts["uploads"]+=1
            except OSError: pass
    if work.is_dir():
        cutoff=_cutoff_days(failed_workspace_hours/24.0,now)
        for job_dir in work.iterdir():
            if not job_dir.is_dir(): continue
            for attempt_dir in job_dir.iterdir():
                try:
                    if attempt_dir.is_dir() and attempt_dir.resolve() not in active and attempt_dir.stat().st_mtime<cutoff:
                        shutil.rmtree(attempt_dir); counts["failed_workspaces"]+=1
                except OSError: pass
    return counts

def main(argv: Iterable[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description="Edit Factory retention janitor")
    parser.add_argument("--db",default=os.environ.get("AIVF_DB_PATH","state/jobs.db"))
    parser.add_argument("--uploads",default=os.environ.get("AIVF_UPLOAD_DIR","uploads"))
    parser.add_argument("--output",default=os.environ.get("AIVF_OUTPUT_DIR","output"))
    parser.add_argument("--workspace",default=None)
    parser.add_argument("--prune-terminal-jobs",action="store_true")
    args=parser.parse_args(list(argv) if argv is not None else None)
    print(json.dumps(run_janitor(db_path=args.db,upload_root=args.uploads,output_root=args.output,workspace_root=args.workspace,prune_terminal_jobs=args.prune_terminal_jobs),sort_keys=True))
    return 0

if __name__=="__main__": raise SystemExit(main())
