"""Queue backend abstraction suitable for one or many worker processes."""
from __future__ import annotations
import json
import sqlite3
import time
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class QueueJob:
    job_id: str
    payload: dict[str,Any]
    created_at: float
    worker_token: str

class SQLiteJobBackend:
    """Minimal durable queue with atomic claim and visibility timeout."""

    def __init__(self,path:str|Path,visibility_timeout:float=3600.0)->None:
        if visibility_timeout<=0: raise ValueError("visibility_timeout must be positive")
        self.path=str(Path(path))
        self.visibility_timeout=float(visibility_timeout)
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS queue (job_id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,claimed REAL,worker_token TEXT)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(queue)").fetchall()}
            if "worker_token" not in columns:
                conn.execute("ALTER TABLE queue ADD COLUMN worker_token TEXT")

    def _connect(self)->sqlite3.Connection:
        conn=sqlite3.connect(self.path,timeout=10)
        conn.execute("PRAGMA journal_mode=WAL"); conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def enqueue(self,job_id:str,payload:dict[str,Any])->None:
        with self._connect() as conn:
            conn.execute("INSERT INTO queue(job_id,payload,status,created,claimed) VALUES(?,?,?,?,NULL)",(job_id,json.dumps(payload), "queued",time.time()))

    def claim(self)->QueueJob|None:
        with self._connect() as conn:
            now=time.time()
            conn.execute("BEGIN IMMEDIATE")
            row=conn.execute("SELECT job_id,payload,created FROM queue WHERE status='queued' OR (status='running' AND claimed<?) ORDER BY created LIMIT 1",(now-self.visibility_timeout,)).fetchone()
            if not row: conn.rollback(); return None
            worker_token = uuid4().hex
            conn.execute("UPDATE queue SET status='running',claimed=?,worker_token=? WHERE job_id=?",(now,worker_token,row[0]))
            return QueueJob(row[0],json.loads(row[1]),float(row[2]),worker_token)

    def heartbeat(self,job_id:str,worker_token:str)->bool:
        with self._connect() as conn:
            return conn.execute("UPDATE queue SET claimed=? WHERE job_id=? AND status='running' AND worker_token=?",(time.time(),job_id,str(worker_token))).rowcount==1

    def complete(self,job_id:str,worker_token:str)->bool:
        with self._connect() as conn:
            return conn.execute("UPDATE queue SET status='done',claimed=NULL,worker_token=NULL WHERE job_id=? AND status='running' AND worker_token=?",(job_id,str(worker_token))).rowcount==1

    def fail(self,job_id:str,worker_token:str,error:str)->bool:
        with self._connect() as conn:
            row=conn.execute("SELECT payload FROM queue WHERE job_id=? AND status='running' AND worker_token=?",(job_id,str(worker_token))).fetchone()
            if not row:
                return False
            payload=json.loads(row[0])
            payload["error"]=str(error)[:4000]
            return conn.execute("UPDATE queue SET status='failed',claimed=NULL,worker_token=NULL,payload=? WHERE job_id=? AND status='running' AND worker_token=?",(json.dumps(payload),job_id,str(worker_token))).rowcount==1

    def recover_expired(self)->int:
        with self._connect() as conn:
            cutoff=time.time()-self.visibility_timeout
            return int(conn.execute("UPDATE queue SET status='queued',claimed=NULL,worker_token=NULL WHERE status='running' AND claimed<?",(cutoff,)).rowcount)

__all__=["QueueJob","SQLiteJobBackend"]
