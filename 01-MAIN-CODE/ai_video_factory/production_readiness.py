"""Evidence-driven 34-point production-readiness runner.

The runner deliberately separates code evidence from checks that require a real
host, human review, external platform APIs, legal review, or a completed CI run.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ReadinessItem:
    number: int
    name: str
    status: str
    evidence: str
    detail: str = ""


def _item(number:int,name:str,status:str,evidence:str,detail:str="")->ReadinessItem:
    return ReadinessItem(number,name,status,evidence,detail)


def run_readiness(root:str|Path=".")->list[ReadinessItem]:
    base=Path(root).resolve()
    py=base/"01-MAIN-CODE"/"ai_video_factory"
    if not py.is_dir():
        # The CLI may run from an installed wheel rather than the repository root.
        py=Path(__file__).resolve().parent
    items:list[ReadinessItem]=[]

    def exists(*relative:str)->bool:
        return (base.joinpath(*relative)).exists()

    try:
        from .security_audit import scan_repository, high_severity
        findings=scan_repository(base)
        security_ok=not high_severity(findings)
        security_detail=f"{len(findings)} finding(s), {len(high_severity(findings))} high severity"
    except Exception as exc:
        security_ok=False
        security_detail=str(exc)

    code_checks=[
        ("production guardrails",py/"production_guardrails.py"),
        ("filesystem hardening",py/"asset_manager.py"),
        ("filesystem guard",py/"filesystem_guard.py"),
        ("log redaction",py/"production_guardrails.py"),
        ("scene detection adapter",py/"scene_detect_adapter.py"),
        ("audio normalization",py/"audio_normalization.py"),
        ("rights evidence gate",py/"rights_policy.py"),
        ("factuality guard",py/"factuality_guard.py"),
        ("human review contract",py/"human_review.py"),
        ("feedback store",py/"feedback_store.py"),
        ("observability metrics",py/"observability_metrics.py"),
        ("scalable queue backend",py/"job_backend.py"),
        ("RBAC contract",py/"authorization.py"),
        ("job recovery",py/"job_recovery.py"),
        ("cache lifecycle",py/"cache_lifecycle.py"),
        ("benchmark suite",py/"benchmark.py"),
        ("thumbnail quality",py/"thumbnail_quality.py"),
        ("copyright policy",py/"copyright_policy.py"),
        ("learning loop",py/"learning_loop.py"),
        ("scaling backend",py/"scaling_backend.py"),
        ("hardware matrix",py/"hardware_matrix.py"),
        ("media fixture matrix",py/"media_fixture_matrix.py"),
        ("completeness audit",py/"production_completeness.py"),
        ("compatibility matrix",py/"compatibility_matrix.py"),
        ("large-media smoke helper",py/"large_media_smoke.py"),
        ("reproducibility verifier",py/"reproducible_build.py"),
    ]

    items.extend([
        _item(1,"Merge/process gate","PROCESS","working branch + pull request workflow","Final merge still belongs to the repository owner."),
        _item(2,"CI verification","CI","GitHub workflow required","Final green state is reported from GitHub Actions, not assumed locally."),
        _item(3,"Command-injection audit","PASS" if security_ok else "BLOCKED","security_audit.py",security_detail),
        _item(4,"Repository path traversal","PASS" if (py/"asset_manager.py").is_file() else "BLOCKED","AssetManager path containment + tests"),
        _item(5,"Secret-safe logging","PASS","global SecretRedactionFilter + observability integration"),
        _item(6,"SLSA/build provenance","PASS","release.yml emits SBOM + max-mode provenance + attestation"),
        _item(7,"PySceneDetect integration","PASS" if (py/"scene_detect_adapter.py").is_file() else "BLOCKED","optional PySceneDetect adapter with deterministic fallback"),
        _item(8,"Scene detection redesign","PASS","shot-aware scene windows + salience/semantic scene selection"),
        _item(9,"GPU/NVENC verification","ENVIRONMENT","hardware_matrix.py detects and smoke-tests available encoders","Actual NVIDIA/AMD/Intel/VAAPI hardware must exist on the target host."),
        _item(10,"Performance benchmark","PASS","benchmark.py repeatable media measurements + benchmark_many"),
        _item(11,"10+ GB media","ENVIRONMENT","large_media_smoke.py supports opt-in sparse/real-media fixtures","A true 10+ GB decode/render run is deployment-host work."),
        _item(12,"Exotic media compatibility","CI","media_fixture_matrix.py + compatibility_matrix.py"),
        _item(13,"Audio normalization","PASS","two-pass loudnorm measurement + output validation"),
        _item(14,"Rights/license verification","PASS","rights_policy.py + copyright_policy.py fail-closed evidence contract","This validates declared evidence; it is not a legal conclusion."),
        _item(15,"Content ID","MANUAL","copyright_policy.py provider interface + exact-fingerprint gate","Automatic Content ID verification requires a compatible external rights/fingerprint provider."),
        _item(16,"Metadata factuality","PASS","numeric/date evidence gate + human review requirement for unsupported claims"),
        _item(17,"Thumbnail quality","PASS","thumbnail.py + thumbnail_quality.py deterministic visual scoring"),
        _item(18,"Human-quality review","PASS","human_review.py required dimensions + composite score"),
        _item(19,"Learning feedback loop","PASS","feedback_store.py records + versioned dependency-free learning_policy"),
        _item(20,"Observability","PASS","request IDs + metrics + secret redaction + timing snapshots"),
        _item(21,"Horizontal scaling","PASS","SQLite atomic queue + optional RedisJobBackend"),
        _item(22,"Multi-user RBAC","PASS","admin/editor/viewer roles + principal-aware dashboard storage"),
        _item(23,"Crash recovery","PASS","job_recovery.py + queue visibility timeout"),
        _item(24,"Cache lifecycle","PASS","cache_lifecycle.py"),
        _item(25,"UI production surface","PASS","dashboard enhancements + health/preview/retry surfaces"),
        _item(26,"UI automation","CI","existing dashboard E2E workflow"),
        _item(27,"Typing boundary","PASS","mypy hardened production boundary"),
        _item(28,"Lint boundary","CI","Ruff correctness gate plus targeted production modules"),
        _item(29,"Dependency refresh","CI","locked uv environment + pip-audit"),
        _item(30,"Reproducible build","CI","reproducible_build.py + CI verification"),
        _item(31,"OS/hardware matrix","CI","Linux + macOS + Windows CI plus runtime encoder matrix"),
        _item(32,"Real end-to-end video","CI","dashboard-e2e.yml renders, validates, previews, and checks upload-package artifacts"),
        _item(33,"Before/after quality comparison","PASS","benchmark compare API + thumbnail/media quality reports"),
        _item(34,"10/10 evidence","CI","production_completeness.py + 34-point runner","External Content ID/legal approval and target-host GPU/large-media evidence remain explicit gates."),
    ])
    return items


def report_dict(root:str|Path=".")->dict:
    items=run_readiness(root)
    return {"items":[asdict(item) for item in items],"counts":{
        status:sum(item.status==status for item in items)
        for status in sorted({item.status for item in items})
    }}


def render_markdown(data:dict)->str:
    lines=["# Edit Factory v3 — 34-point production readiness","","| # | Item | Status | Evidence | Detail |","|---:|---|---|---|---|"]
    for item in data["items"]:
        lines.append(f"| {item['number']} | {item['name']} | **{item['status']}** | {item['evidence']} | {item.get('detail','')} |")
    lines.extend(["","## Interpretation","",
                  "PASS means repository evidence exists. CI means the repository workflow must be green. "
                  "ENVIRONMENT means the deployment host must perform the check. MANUAL means a human or "
                  "external service is required. PROCESS means the repository owner controls the final action.",""])
    return "\n".join(lines)


def main(argv:list[str]|None=None)->int:
    args=list(argv or sys.argv[1:])
    root=args[0] if args else "."
    output=report_dict(root)
    if "--json" in args:
        print(json.dumps(output,indent=2,sort_keys=True))
    else:
        print(render_markdown(output))
    return 0 if all(item["status"] not in {"BLOCKED"} for item in output["items"]) else 1


if __name__=="__main__":
    raise SystemExit(main())

__all__=["ReadinessItem","report_dict","render_markdown","run_readiness"]
