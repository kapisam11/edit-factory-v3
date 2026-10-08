"""Strict production release gate."""
from __future__ import annotations
import argparse,json,os,re
from pathlib import Path
from .db_migrations import SchemaMismatch,verify_database_schema

def _hash_ok(value:str)->bool: return bool(re.fullmatch(r"[0-9a-fA-F]{64}",value.strip()))

def check_release(root:str|Path=".")->dict[str,object]:
    base=Path(root).resolve(); checks={}
    for env_name,name in (("AIVF_CI_STATUS","ci_green"),("AIVF_SECURITY_STATUS","security_green"),("AIVF_E2E_STATUS","e2e_green"),("AIVF_TARGET_HOST_SMOKE","target_host_smoke"),("AIVF_BACKUP_OFFHOST_VERIFIED","backup_offhost_verified")):
        raw=os.environ.get(env_name,"").strip().lower()
        checks[name]={"ok":raw in {"green","pass","passed","true","1"},"detail":raw or "unset"}
    human=os.environ.get("AIVF_HUMAN_APPROVAL","").strip().lower()
    checks["human_approval"]={"ok":human in {"approved","true","1"},"detail":human or "unset"}
    image=os.environ.get("AIVF_IMAGE","").strip()
    checks["image_pinned"]={"ok":bool(re.fullmatch(r".+@sha256:[0-9a-f]{64}",image)),"detail":image or "unset"}

    lock_file=Path(os.environ.get("AIVF_MODEL_LOCK_FILE",str(base/"06-CONFIG-AND-DEPLOYMENT"/"model-lock.json")))
    lock_ok=False
    lock_detail=str(lock_file)
    if lock_file.is_file():
        try:
            payload=json.loads(lock_file.read_text(encoding="utf-8"))
            assets=payload.get("assets",[])
            immutable=str(payload.get("immutable_commit",""))
            lock_ok=(
                bool(re.fullmatch(r"[0-9a-f]{40}",immutable))
                and isinstance(assets,list)
                and len(assets)>=2
                and all(
                    isinstance(x,dict)
                    and _hash_ok(str(x.get("sha256","")))
                    and str(x.get("relative_path","")).strip()
                    and str(x.get("url","")).strip()
                    for x in assets
                )
            )
        except (OSError,ValueError,TypeError,json.JSONDecodeError) as exc:
            lock_detail=str(exc)
            lock_ok=False
    checks["model_lock_file"]={"ok":lock_ok,"detail":lock_detail}

    try:
        from .asset_manager import verify_runtime_asset, RUNTIME_ASSETS
        actual_results=[verify_runtime_asset(spec, base) for spec in RUNTIME_ASSETS]
        hash_ok=lock_ok and all(actual_results)
        detail="runtime model bytes match the locked SHA-256 digests"
    except Exception as exc:
        hash_ok=False
        detail=f"runtime model verification unavailable: {exc}"
    checks["model_hashes_pinned"]={"ok":hash_ok,"detail":detail}

    db=Path(os.environ.get("AIVF_DB_PATH",str(base/"state"/"jobs.db")))
    try:
        verify_database_schema(db); checks["database_migrated"]={"ok":True,"detail":str(db)}
    except (SchemaMismatch,OSError,ValueError) as exc:
        checks["database_migrated"]={"ok":False,"detail":str(exc)}

    marker=Path(os.environ.get("AIVF_BACKUP_RESTORE_MARKER",str(base/"state"/"backup-restore-verified")))
    checks["backup_restore_verified"]={"ok":marker.is_file(),"detail":str(marker)}
    offhost_marker=Path(os.environ.get("AIVF_BACKUP_OFFHOST_MARKER",str(base/"state"/"backups"/"offhost-verified")))
    checks["backup_offhost_marker"]={"ok":offhost_marker.is_file(),"detail":str(offhost_marker)}
    required=(
        base/"06-CONFIG-AND-DEPLOYMENT"/"deploy_with_rollback.sh",
        base/"06-CONFIG-AND-DEPLOYMENT"/"target_recovery_smoke.sh",
        base/"03-SIDE-CODE"/"tools"/"load_test_admission.py",
        base/"03-SIDE-CODE"/"tools"/"load_test_real_render.py",
        base/"03-SIDE-CODE"/"tools"/"verify_model_lock.py",
        base/"01-MAIN-CODE"/"ai_video_factory"/"retention_janitor.py",
        base/"01-MAIN-CODE"/"ai_video_factory"/"backup_restore.py",
    )
    checks["operational_tooling_present"]={"ok":all(p.is_file() for p in required),"detail":"rollback/recovery/load/model-verification/retention/backup tooling present"}

    failures=[name for name,value in checks.items() if not bool(value["ok"])]
    return {"ok":not failures,"failures":failures,"checks":checks}

def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description="Edit Factory strict production release gate")
    parser.add_argument("root",nargs="?",default="."); parser.add_argument("--json",action="store_true")
    args=parser.parse_args(argv); result=check_release(args.root)
    if args.json: print(json.dumps(result,indent=2,sort_keys=True))
    else:
        for name,value in result["checks"].items(): print(f"{name}: {'PASS' if value['ok'] else 'BLOCKED'} — {value['detail']}")
        print("release gate: PASS" if result["ok"] else "release blocked: "+", ".join(result["failures"]))
    return 0 if result["ok"] else 1
__all__=["check_release","main"]
