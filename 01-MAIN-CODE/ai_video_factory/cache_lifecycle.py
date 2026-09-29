"""Bounded filesystem cache lifecycle: TTL, LRU and orphan cleanup."""
from __future__ import annotations
import os
import time
from pathlib import Path
from typing import Iterable

class CacheLifecycle:
    def __init__(self,root:str|Path,max_bytes:int=5*1024**3,ttl_seconds:float=86400.0)->None:
        self.root=Path(root).resolve()
        self.max_bytes=max(1,int(max_bytes))
        self.ttl_seconds=max(1.0,float(ttl_seconds))
        self.root.mkdir(parents=True,exist_ok=True)

    def _files(self)->list[Path]:
        return [p for p in self.root.rglob("*") if p.is_file()]

    def purge_expired(self,now:float|None=None)->int:
        now=time.time() if now is None else float(now)
        removed=0
        for path in self._files():
            try:
                if now-path.stat().st_mtime>self.ttl_seconds:
                    path.unlink(missing_ok=True); removed+=1
            except OSError:
                continue
        return removed

    def enforce_size(self)->int:
        files=[]
        total=0
        for path in self._files():
            try:
                size=path.stat().st_size; total+=size; files.append((path,size,path.stat().st_mtime))
            except OSError:
                continue
        removed=0
        for path,size,_ in sorted(files,key=lambda item:item[2]):
            if total<=self.max_bytes: break
            try:
                path.unlink(missing_ok=True); total-=size; removed+=1
            except OSError:
                continue
        return removed

    def remove_orphans(self,referenced:Iterable[str|Path])->int:
        roots={Path(p).resolve() for p in referenced}
        removed=0
        for path in self._files():
            try:
                resolved=path.resolve()
                if resolved in roots:
                    continue
                if path.suffix in {".part",".partial",".tmp"} or path.name.startswith(".aivf-"):
                    path.unlink(missing_ok=True); removed+=1
            except OSError:
                continue
        return removed

    def cleanup(self,referenced:Iterable[str|Path]=())->dict[str,int]:
        expired=self.purge_expired()
        orphans=self.remove_orphans(referenced)
        sized=self.enforce_size()
        return {"expired":expired,"orphans":orphans,"size_evictions":sized}

__all__=["CacheLifecycle"]
