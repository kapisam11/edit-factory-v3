"""Durable, provider-neutral performance feedback storage."""
from __future__ import annotations
import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Mapping

@dataclass(frozen=True)
class FeedbackRecord:
    video_id: str
    platform: str
    edit_type: str
    observed_at: str
    metrics: dict[str,float]
    metadata: dict[str,Any]

class FeedbackStore:
    def __init__(self,path:str|Path)->None:
        self.path=str(Path(path))
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS feedback (video_id TEXT PRIMARY KEY, platform TEXT NOT NULL, edit_type TEXT NOT NULL, observed_at TEXT NOT NULL, metrics TEXT NOT NULL, metadata TEXT NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_platform_edit ON feedback(platform,edit_type)")

    def _connect(self)->sqlite3.Connection:
        conn=sqlite3.connect(self.path,timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def record(self,record:FeedbackRecord)->None:
        with self._connect() as conn:
            conn.execute("INSERT INTO feedback(video_id,platform,edit_type,observed_at,metrics,metadata) VALUES(?,?,?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET platform=excluded.platform,edit_type=excluded.edit_type,observed_at=excluded.observed_at,metrics=excluded.metrics,metadata=excluded.metadata",
                         (record.video_id,record.platform,record.edit_type,record.observed_at,json.dumps(record.metrics),json.dumps(record.metadata)))

    def add(self,video_id:str,platform:str,edit_type:str,observed_at:str,metrics:Mapping[str,float],metadata:Mapping[str,Any]|None=None)->None:
        values={str(k):float(v) for k,v in metrics.items()}
        self.record(FeedbackRecord(video_id,platform,edit_type,observed_at,values,dict(metadata or {})))

    def aggregate(self,platform:str|None=None)->dict[str,Any]:
        sql="SELECT platform,edit_type,metrics FROM feedback"; params: tuple[str, ...] = ()
        if platform: sql+=" WHERE platform=?"; params=(platform,)
        with self._connect() as conn: rows=conn.execute(sql,params).fetchall()
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for plat,edit,raw in rows:
            metrics=json.loads(raw)
            key=(plat,edit); bucket=groups.setdefault(key,[])
            bucket.append(metrics)
        result: dict[str, Any] = {}
        for (plat,edit),bucket in groups.items():
            keys=set().union(*(item.keys() for item in bucket))
            result[f"{plat}:{edit}"]={"samples":len(bucket),"means":{k:round(sum(float(item.get(k,0.0)) for item in bucket)/len(bucket),6) for k in keys}}
        return result

__all__=["FeedbackRecord","FeedbackStore"]
