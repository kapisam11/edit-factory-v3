"""Explicit stale-worker recovery primitives."""
from __future__ import annotations
import os
import signal
import time
from typing import Callable

def process_is_alive(pid:int)->bool:
    if int(pid)<=0: return False
    try:
        os.kill(int(pid),0)
    except OSError:
        return False
    return True

def should_recover(*, heartbeat_epoch:float|None,last_seen_epoch:float,now:float|None=None,timeout_seconds:float=3600.0)->bool:
    current=time.time() if now is None else float(now)
    if heartbeat_epoch is not None:
        last_seen_epoch=float(heartbeat_epoch)
    return current-last_seen_epoch>float(timeout_seconds)

def reconcile_job(*, job_id:str,pid:int|None,heartbeat_epoch:float|None,last_seen_epoch:float,mark_interrupted:Callable[[str,str],None],timeout_seconds:float=3600.0)->bool:
    """Recover only when a lease is stale and the recorded worker is not alive."""
    if not should_recover(heartbeat_epoch=heartbeat_epoch,last_seen_epoch=last_seen_epoch,timeout_seconds=timeout_seconds):
        return False
    if pid is not None and process_is_alive(int(pid)):
        return False
    mark_interrupted(job_id,"worker heartbeat expired and worker process is not running")
    return True

__all__=["process_is_alive","should_recover","reconcile_job"]
