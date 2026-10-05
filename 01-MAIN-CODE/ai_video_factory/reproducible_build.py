"""Build reproducibility verifier."""
from __future__ import annotations
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

class ReproducibilityError(RuntimeError):
    pass

def sha256(path:Path)->str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()

def build_twice(project_root:str|Path,python_executable:str|None=None)->dict[str,object]:
    root=Path(project_root).resolve()
    executable=python_executable or sys.executable
    epoch=os.environ.get("SOURCE_DATE_EPOCH","1700000000")
    with tempfile.TemporaryDirectory(prefix="aivf-repro-") as tmp:
        first=Path(tmp)/"first"; second=Path(tmp)/"second"
        first.mkdir(); second.mkdir()
        env=dict(os.environ); env["SOURCE_DATE_EPOCH"]=str(epoch)
        for out in (first,second):
            result=subprocess.run([executable,"-m","build","--wheel","--no-isolation","--outdir",str(out),str(root)],cwd=str(root),env=env,capture_output=True,text=True,timeout=900,check=False)
            if result.returncode!=0: raise ReproducibilityError((result.stderr or result.stdout)[-4000:])
        a=next(first.glob("*.whl"),None); b=next(second.glob("*.whl"),None)
        if not a or not b: raise ReproducibilityError("two wheel artifacts were not produced")
        return {"first_sha256":sha256(a),"second_sha256":sha256(b),"byte_identical":sha256(a)==sha256(b),"filename_equal":a.name==b.name}

__all__=["ReproducibilityError","build_twice","sha256"]
