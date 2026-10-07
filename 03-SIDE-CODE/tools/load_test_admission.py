#!/usr/bin/env python3
"""SQLite admission/idempotency load test."""
from __future__ import annotations
import argparse,json,statistics,tempfile,threading,time
from pathlib import Path
from dashboard_store import DashboardStore

def run(count:int, identical:bool)->dict[str,object]:
    with tempfile.TemporaryDirectory(prefix="aivf-load-") as tmp:
        store=DashboardStore(Path(tmp)/"jobs.db"); store.ensure_indexes()
        latencies=[]; errors=[]; lock=threading.Lock()
        def submit(index:int):
            start=time.perf_counter()
            try:
                store.insert_job_idempotent(
                    f"job-{index}-{time.time_ns()}","load-test",{"topic":"load-test"},
                    principal="load-test",idempotency_key=("same" if identical else f"k-{index}"),
                    request_hash=("same-request" if identical else f"r-{index}"),
                    max_queued_jobs=count+10,principal_limit=count+10)
            except Exception as exc:
                with lock: errors.append(type(exc).__name__)
            finally:
                with lock: latencies.append((time.perf_counter()-start)*1000)
        threads=[threading.Thread(target=submit,args=(i,)) for i in range(count)]
        started=time.perf_counter()
        for t in threads: t.start()
        for t in threads: t.join()
        elapsed=time.perf_counter()-started
        with store.connect() as conn: unique=int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        return {"requests":count,"identical":identical,"unique_jobs":unique,"errors":len(errors),"throughput_rps":round(count/elapsed,2) if elapsed else 0,"p50_ms":round(statistics.median(latencies),2) if latencies else 0,"max_ms":round(max(latencies),2) if latencies else 0}

if __name__=="__main__":
    p=argparse.ArgumentParser(); p.add_argument("--requests",type=int,default=50); p.add_argument("--identical",action="store_true"); a=p.parse_args()
    if not 1<=a.requests<=1000: raise SystemExit("--requests must be between 1 and 1000")
    report=run(a.requests,a.identical); print(json.dumps(report,indent=2,sort_keys=True))
    raise SystemExit(0 if report["errors"]==0 and (not a.identical or report["unique_jobs"]==1) else 1)
