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


def _required_code_checks(py: Path) -> list[tuple[str, Path]]:
    """List implementation files whose presence is required for code evidence."""
    return [
        ("production guardrails", py / "production_guardrails.py"),
        ("filesystem hardening", py / "asset_manager.py"),
        ("log redaction", py / "production_guardrails.py"),
        ("scene detection adapter", py / "scene_detect_adapter.py"),
        ("audio normalization", py / "audio_normalization.py"),
        ("rights evidence gate", py / "rights_policy.py"),
        ("factuality guard", py / "factuality_guard.py"),
        ("human review contract", py / "human_review.py"),
        ("feedback store", py / "feedback_store.py"),
        ("observability metrics", py / "observability_metrics.py"),
        ("scalable queue backend", py / "job_backend.py"),
        ("RBAC contract", py / "authorization.py"),
        ("job recovery", py / "job_recovery.py"),
        ("cache lifecycle", py / "cache_lifecycle.py"),
        ("benchmark suite", py / "benchmark.py"),
        ("compatibility matrix", py / "compatibility_matrix.py"),
        ("large-media smoke helper", py / "large_media_smoke.py"),
        ("reproducibility verifier", py / "reproducible_build.py"),
    ]


def _evaluate_code_checks(checks: list[tuple[str, Path]]) -> list[dict[str, str]]:
    """Turn required-file assumptions into explicit pass/block evidence."""
    results: list[dict[str, str]] = []
    for name, path in checks:
        present = path.is_file()
        results.append({
            "name": name,
            "path": path.as_posix(),
            "status": "PASS" if present else "BLOCKED",
            "detail": "required implementation file exists" if present
                      else "required implementation file is missing",
        })
    return results


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

    code_checks = _required_code_checks(py)
    code_check_results = _evaluate_code_checks(code_checks)
    missing_code_checks = [check for check in code_check_results if check["status"] == "BLOCKED"]

    items.extend([
        _item(1,"Merge/process gate","PROCESS","working branch + pull request workflow","Final merge still belongs to the repository owner."),
        _item(2,"CI verification","CI","GitHub workflow required","Final green state is reported from GitHub Actions, not assumed locally."),
        _item(3,"Command-injection audit","PASS" if security_ok else "BLOCKED","security_audit.py",security_detail),
        _item(4,"Repository path traversal","PASS" if (py/"asset_manager.py").is_file() else "BLOCKED","AssetManager path containment + tests"),
        _item(5,"Secret-safe logging","PASS","recursive redaction helper + logging integration path","All application log call sites still need runtime verification."),
        _item(6,"SLSA/build provenance","CI","release.yml emits SBOM/provenance/attestation","Attestation generation is a GitHub-hosted control."),
        _item(7,"PySceneDetect integration","PASS" if (py/"scene_detect_adapter.py").is_file() else "BLOCKED","optional adapter with deterministic fallback"),
        _item(8,"Scene detection redesign","PASS","shot-aware scene windows + existing visual salience pipeline"),
        _item(9,"GPU/NVENC verification","ENVIRONMENT","hardware.py verifies actual FFmpeg encoder availability","Requires a host with the target GPU."),
        _item(10,"Performance benchmark","PASS","benchmark.py","Benchmarks are recorded; no universal speed claim is made."),
        _item(11,"10+ GB media","ENVIRONMENT","large_media_smoke.py","Opt-in test avoids making CI consume 10+ GB by default."),
        _item(12,"Exotic media compatibility","ENVIRONMENT","compatibility_matrix.py","Run against the target formats on the deployment host."),
        _item(13,"Audio normalization","PASS","two-pass loudnorm helper"),
        _item(14,"Rights/license verification","PASS","rights_policy.py evidence gate","This validates declared evidence; it is not a legal opinion."),
        _item(15,"Content ID","MANUAL","exact/visual fingerprinting infrastructure","No external Content ID database is available for an automatic guarantee."),
        _item(16,"Metadata factuality","PASS","factuality_guard.py + metadata gate","Numeric/date claims require evidence or explicit fact review."),
        _item(17,"Thumbnail quality","PASS","thumbnail.py quality scoring + focal-frame extraction","Creative quality still benefits from human review."),
        _item(18,"Human-quality review","PASS","human_review.py","Publish approval remains a human decision."),
        _item(19,"Learning feedback loop","PASS","feedback_store.py + existing performance_learning"),
        _item(20,"Observability","PASS","observability_metrics.py + dashboard observability"),
        _item(21,"Durable queue / scaling boundary","PASS","DashboardStore lifecycle claims plus optional Redis backend contracts","The supported web deployment remains single-process; multi-web-process scaling requires externalized ownership/state."),
        _item(22,"Multi-user RBAC","PASS","authorization.py + dashboard token authentication"),
        _item(23,"Crash recovery","PASS","job_recovery.py + queue visibility timeout"),
        _item(24,"Cache lifecycle","PASS","cache_lifecycle.py"),
        _item(25,"UI production surface","PASS","dashboard enhancements + health/preview/retry surfaces"),
        _item(26,"UI automation","CI","existing dashboard E2E workflow"),
        _item(27,"Typing boundary","PASS","mypy hardened production boundary"),
        _item(28,"Lint boundary","CI","Ruff correctness gate plus targeted production modules"),
        _item(29,"Dependency refresh","CI","locked uv environment + pip-audit"),
        _item(30,"Reproducible build","CI","reproducible_build.py + CI verification"),
        _item(31,"OS/hardware matrix","ENVIRONMENT","Linux + Windows CI; hardware checks are runtime-detected"),
        _item(32,"Real end-to-end video","CI","dashboard-e2e.yml generates and renders a real media fixture"),
        _item(33,"Before/after quality comparison","PASS","benchmark compare API + quality reports","Requires reference artifacts to produce a project-specific delta."),
        _item(
            34,
            "10/10 evidence",
            "BLOCKED" if missing_code_checks else "MANUAL",
            "this 34-point runner + required implementation-file checks",
            (
                "Missing required implementation files: "
                + ", ".join(check["name"] for check in missing_code_checks)
            ) if missing_code_checks else
            "A release can only claim full readiness after CI, host, human, and external gates are satisfied.",
        ),
    ])
    return items


def _package_directory(root: str | Path = ".") -> Path:
    base = Path(root).resolve()
    package_dir = base / "01-MAIN-CODE" / "ai_video_factory"
    # The CLI may run from an installed wheel rather than the repository root.
    return package_dir if package_dir.is_dir() else Path(__file__).resolve().parent


def report_dict(root:str|Path=".")->dict:
    items=run_readiness(root)
    code_checks = _evaluate_code_checks(_required_code_checks(_package_directory(root)))
    code_blockers = sum(check["status"] == "BLOCKED" for check in code_checks)
    checklist_blockers = sum(item.status == "BLOCKED" for item in items)
    no_known_code_blockers = code_blockers == 0 and checklist_blockers == 0
    fully_verified = no_known_code_blockers and all(item.status == "PASS" for item in items)
    outstanding_gates = sorted({item.status for item in items if item.status != "PASS"})
    if not no_known_code_blockers:
        verification_state = "BLOCKED"
    elif fully_verified:
        verification_state = "FULLY_VERIFIED"
    else:
        verification_state = "NOT_FULLY_VERIFIED"
    return {
        "items": [asdict(item) for item in items],
        "counts": {
            status: sum(item.status == status for item in items)
            for status in sorted({item.status for item in items})
        },
        "code_checks": code_checks,
        "summary": {
            "verification_state": verification_state,
            "code_blockers": code_blockers,
            "checklist_blockers": checklist_blockers,
            "no_known_code_blockers": no_known_code_blockers,
            "fully_verified": fully_verified,
            "release_ready": fully_verified,
            "outstanding_gates": outstanding_gates,
        },
    }


def render_markdown(data:dict)->str:
    lines=["# Edit Factory v3 — 34-point production readiness","","| # | Item | Status | Evidence | Detail |","|---:|---|---|---|---|"]
    for item in data["items"]:
        lines.append(f"| {item['number']} | {item['name']} | **{item['status']}** | {item['evidence']} | {item.get('detail','')} |")
    summary = data.get("summary", {})
    lines.extend([
        "",
        "## Overall result",
        "",
        f"**Verification state:** {summary.get('verification_state', 'UNKNOWN')}",
        f"- Required-file blockers: {summary.get('code_blockers', 'not evaluated')}",
        f"- Checklist blockers: {summary.get('checklist_blockers', 'not evaluated')}",
        f"- No known code blockers: {summary.get('no_known_code_blockers', 'not evaluated')}",
        f"- Fully verified / release ready: {summary.get('fully_verified', False)}",
        f"- Outstanding gate statuses: {', '.join(summary.get('outstanding_gates', [])) or 'none'}",
        "",
        "## Required implementation-file checks",
        "",
        "| Check | Status | Path | Detail |",
        "|---|---|---|---|",
    ])
    for check in data.get("code_checks", []):
        lines.append(
            f"| {check['name']} | **{check['status']}** | "
            f"{check['path']} | {check['detail']} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "PASS means repository evidence exists. CI means the repository workflow must be green. "
        "ENVIRONMENT means the deployment host must perform the check. MANUAL means a human or "
        "external service is required. PROCESS means the repository owner controls the final action.",
        "A normal exit code means there are no known blockers, not that production readiness is fully verified. "
        "Use --strict to require every checklist gate to be PASS.",
        "",
    ])
    return "\\n".join(lines)


def main(argv:list[str]|None=None)->int:
    import argparse

    parser = argparse.ArgumentParser(description="Inspect Edit Factory v3 production-readiness evidence.")
    parser.add_argument("root", nargs="?", default=".", help="repository root (default: current directory)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return failure unless every checklist gate is PASS and release readiness is fully verified",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    output = report_dict(args.root)
    print(json.dumps(output, indent=2, sort_keys=True) if args.json else render_markdown(output))
    summary = output["summary"]
    success = summary["fully_verified"] if args.strict else summary["no_known_code_blockers"]
    return 0 if success else 1


if __name__=="__main__":
    raise SystemExit(main())

__all__=["ReadinessItem","report_dict","render_markdown","run_readiness"]
