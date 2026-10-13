"""Evidence-driven 34-point production-readiness runner.

The runner deliberately separates code evidence from checks that require a real
host, human review, external platform APIs, legal review, or a completed CI run.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReadinessItem:
    number: int
    name: str
    status: str
    evidence: str
    detail: str = ""


def _item(number:int,name:str,status:str,evidence:str,detail:str="")->ReadinessItem:
    return ReadinessItem(number,name,status,evidence,detail)


class InvalidRepositoryRootError(ValueError):
    """Raised when a supplied path is not an Edit Factory repository root."""


def _resolve_repository_root(root: str | Path) -> Path:
    """Resolve and validate a repository root; never silently use the installed package."""
    requested = Path(root).expanduser()
    try:
        base = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InvalidRepositoryRootError(
            f"invalid repository root {str(requested)!r}: path does not exist or cannot be resolved"
        ) from exc
    if not base.is_dir():
        raise InvalidRepositoryRootError(
            f"invalid repository root {str(base)!r}: expected an existing directory"
        )
    package_dir = base / "01-MAIN-CODE" / "ai_video_factory"
    if not (base / "pyproject.toml").is_file() or not package_dir.is_dir():
        raise InvalidRepositoryRootError(
            f"invalid repository root {str(base)!r}: expected pyproject.toml and "
            "01-MAIN-CODE/ai_video_factory; refusing to inspect an installed-package fallback"
        )
    return base


# These targeted tests exercise behavior instead of inferring success from source-file presence.
_BEHAVIOR_TESTS: dict[str, str] = {
    "secret_logging": "04-TESTS/tests/test_production_readiness_closure.py::test_log_redaction_patterns",
    "rights_gate": "04-TESTS/tests/test_production_readiness_closure.py::test_rights_gate_requires_evidence_for_third_party",
    "factuality_gate": "04-TESTS/tests/test_production_readiness_closure.py::test_factuality_blocks_unsupported_numbers",
    "human_review": "04-TESTS/tests/test_production_readiness_closure.py::test_human_review_requires_all_dimensions",
    "feedback_store": "04-TESTS/tests/test_production_readiness_closure.py::test_feedback_store_aggregates",
    "rbac": "04-TESTS/tests/test_production_readiness_closure.py::test_rbac",
    "crash_recovery": "04-TESTS/tests/test_production_readiness_closure.py::test_job_backend_reclaims_expired_and_rejects_stale_worker",
    "cache_lifecycle": "04-TESTS/tests/test_production_readiness_closure.py::test_cache_lifecycle",
    "audio_normalization": "04-TESTS/tests/test_v3_production_contracts.py::test_v3_audio_normalization_replaces_source_only_after_validation",
}


def _run_behavior_evidence(root: Path) -> dict[str, dict[str, str]]:
    """Execute targeted runtime tests and report the observed outcome per behavior."""
    node_ids = list(_BEHAVIOR_TESTS.values())
    missing = [node_id for node_id in node_ids if not (root / node_id.split("::", 1)[0]).is_file()]
    if missing:
        reason = "targeted test evidence unavailable; missing test file(s): " + ", ".join(missing)
        return {
            key: {"status": "UNVERIFIED", "evidence": node_id, "detail": reason}
            for key, node_id in _BEHAVIOR_TESTS.items()
        }

    env = os.environ.copy()
    source_paths = [
        str(root / relative)
        for relative in ("01-MAIN-CODE", "02-WEB-FILES", "03-SIDE-CODE")
        if (root / relative).is_dir()
    ]
    if env.get("PYTHONPATH"):
        source_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(source_paths)
    command = [sys.executable, "-m", "pytest", "-vv", "-q", *node_ids]
    evidence_command = " ".join(command[1:])
    try:
        completed = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, timeout=120, check=False
        )
    except subprocess.TimeoutExpired:
        return {
            key: {"status": "UNVERIFIED", "evidence": node_id,
                  "detail": "targeted runtime tests exceeded the 120-second evidence timeout"}
            for key, node_id in _BEHAVIOR_TESTS.items()
        }
    except OSError as exc:
        return {
            key: {"status": "UNVERIFIED", "evidence": node_id,
                  "detail": f"could not execute targeted runtime tests: {type(exc).__name__}: {exc}"}
            for key, node_id in _BEHAVIOR_TESTS.items()
        }

    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if "No module named pytest" in output or "No module named 'pytest'" in output:
        return {
            key: {"status": "UNVERIFIED", "evidence": node_id,
                  "detail": "pytest is not installed; install the project dev/test dependencies to verify behavior"}
            for key, node_id in _BEHAVIOR_TESTS.items()
        }

    results: dict[str, dict[str, str]] = {}
    for key, node_id in _BEHAVIOR_TESTS.items():
        test_name = node_id.rsplit("::", 1)[-1]
        matching = [
            line.strip() for line in output.splitlines()
            if test_name in line and any(
                marker in line for marker in (" PASSED", " FAILED", " ERROR", " SKIPPED", " XFAIL")
            )
        ]
        outcome = matching[-1] if matching else ""
        if " PASSED" in outcome:
            status = "PASS"
            detail = f"runtime test passed: {outcome}; command: {evidence_command}"
        elif " FAILED" in outcome or " ERROR" in outcome:
            status = "BLOCKED"
            detail = f"runtime test failed: {outcome}; command: {evidence_command}"
        elif " SKIPPED" in outcome or " XFAIL" in outcome:
            status = "UNVERIFIED"
            detail = f"runtime test did not prove behavior: {outcome}; command: {evidence_command}"
        else:
            status = "BLOCKED" if completed.returncode != 0 else "UNVERIFIED"
            detail = (
                f"runtime test produced no parseable per-test result; pytest exit code {completed.returncode}; "
                f"command: {evidence_command}"
            )
        results[key] = {"status": status, "evidence": node_id, "detail": detail}
    return results


def _evidence_item(
    number: int, name: str, evidence_key: str, implementation_evidence: str,
    unverified_detail: str, runtime_evidence: dict[str, dict[str, str]],
) -> ReadinessItem:
    observed = runtime_evidence.get(evidence_key)
    if observed is None:
        return _item(number, name, "UNVERIFIED", implementation_evidence, unverified_detail)
    if observed["status"] in {"PASS", "BLOCKED"}:
        return _item(number, name, observed["status"], observed["evidence"], observed["detail"])
    return _item(
        number, name, "UNVERIFIED", observed["evidence"], observed["detail"] or unverified_detail
    )




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
    """Report file presence without implying the implementation has passed behavior tests."""
    results: list[dict[str, str]] = []
    for name, path in checks:
        present = path.is_file()
        results.append({
            "name": name,
            "path": path.as_posix(),
            "status": "PRESENT" if present else "BLOCKED",
            "detail": "implementation file is present; behavior is not implied" if present
                      else "required implementation file is missing",
        })
    return results


def run_readiness(root: str | Path = ".") -> list[ReadinessItem]:
    base = _resolve_repository_root(root)
    py = base / "01-MAIN-CODE" / "ai_video_factory"
    runtime_evidence = _run_behavior_evidence(base)
    items: list[ReadinessItem] = []

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
        _item(4,"Repository path traversal","UNVERIFIED","asset_manager.py + path-containment tests","Implementation presence is not evidence that traversal cases were executed."),
        _evidence_item(5,"Secret-safe logging","secret_logging","production_guardrails.py","Redaction behavior has not been executed.",runtime_evidence),
        _item(6,"SLSA/build provenance","CI","release.yml emits SBOM/provenance/attestation","Attestation generation is a GitHub-hosted control."),
        _item(7,"PySceneDetect integration","UNVERIFIED" if (py/"scene_detect_adapter.py").is_file() else "BLOCKED","scene_detect_adapter.py","Adapter behavior has not been verified by a targeted runtime test." if (py/"scene_detect_adapter.py").is_file() else "required implementation file is missing"),
        _item(8,"Scene detection redesign","UNVERIFIED","scene detection and salience implementation","Source descriptions alone do not prove shot-aware behavior; an executed integration test is required."),
        _item(9,"GPU/NVENC verification","ENVIRONMENT","hardware.py verifies actual FFmpeg encoder availability","Requires a host with the target GPU."),
        _item(10,"Performance benchmark","UNVERIFIED","benchmark.py","A benchmark file is not proof that a benchmark ran; record a completed benchmark result before PASS."),
        _item(11,"10+ GB media","ENVIRONMENT","large_media_smoke.py","Opt-in test avoids making CI consume 10+ GB by default."),
        _item(12,"Exotic media compatibility","ENVIRONMENT","compatibility_matrix.py","Run against the target formats on the deployment host."),
        _evidence_item(13,"Audio normalization","audio_normalization","audio_normalization.py","Audio normalization behavior has not been executed.",runtime_evidence),
        _evidence_item(14,"Rights/license verification","rights_gate","rights_policy.py","Rights-gate behavior has not been executed; this is not a legal opinion.",runtime_evidence),
        _item(15,"Content ID","MANUAL","exact/visual fingerprinting infrastructure","No external Content ID database is available for an automatic guarantee."),
        _evidence_item(16,"Metadata factuality","factuality_gate","factuality_guard.py","Factuality-gate behavior has not been executed; numeric/date claims require evidence or explicit fact review.",runtime_evidence),
        _item(17,"Thumbnail quality","UNVERIFIED","thumbnail.py quality scoring + focal-frame extraction","Implementation presence is not a measured quality result; a thumbnail-quality test or human evaluation is required."),
        _evidence_item(18,"Human-quality review","human_review","human_review.py","Review-contract behavior has not been executed; publish approval remains a human decision.",runtime_evidence),
        _evidence_item(19,"Learning feedback loop","feedback_store","feedback_store.py","Feedback-store behavior has not been executed.",runtime_evidence),
        _item(20,"Observability","UNVERIFIED","observability_metrics.py + dashboard observability","Implementation references do not prove metrics are emitted and exposed at runtime."),
        _item(21,"Durable queue / scaling boundary","UNVERIFIED","SQLite job lifecycle + optional Redis backend contract","The SQLite recovery probe does not prove multi-process or Redis-backed scaling; that requires a separate integration test."),
        _evidence_item(22,"Multi-user RBAC","rbac","authorization.py","RBAC behavior has not been executed.",runtime_evidence),
        _evidence_item(23,"Crash recovery","crash_recovery","job_recovery.py + SQLite queue visibility timeout","Crash recovery behavior has not been executed.",runtime_evidence),
        _evidence_item(24,"Cache lifecycle","cache_lifecycle","cache_lifecycle.py","Cache lifecycle behavior has not been executed.",runtime_evidence),
        _item(25,"UI production surface","UNVERIFIED","dashboard health/preview/retry surfaces","The presence of UI endpoints does not prove that the complete production flow works."),
        _item(26,"UI automation","CI","existing dashboard E2E workflow"),
        _item(27,"Typing boundary","CI","mypy hardened production boundary","The typing result is established by the configured CI check, not by this runner."),
        _item(28,"Lint boundary","CI","Ruff correctness gate plus targeted production modules"),
        _item(29,"Dependency refresh","CI","locked uv environment + pip-audit"),
        _item(30,"Reproducible build","CI","reproducible_build.py + CI verification"),
        _item(31,"OS/hardware matrix","ENVIRONMENT","Linux + Windows CI; hardware checks are runtime-detected"),
        _item(32,"Real end-to-end video","CI","dashboard-e2e.yml generates and renders a real media fixture"),
        _item(33,"Before/after quality comparison","UNVERIFIED","benchmark compare API + quality reports","No actual before/after result is available; a project-specific comparison is required."),
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
    base = _resolve_repository_root(root)
    return base / "01-MAIN-CODE" / "ai_video_factory"


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
        "PASS means a mapped runtime check was executed and passed. PRESENT means only that an "
        "implementation file exists; it does not prove behavior. UNVERIFIED means no sufficient "
        "runtime evidence was gathered. CI means the repository workflow must be green. ENVIRONMENT "
        "means the deployment host must perform the check. MANUAL means a human or external service "
        "is required. PROCESS means the repository owner controls the final action.",
        "A normal exit code means there are no known blockers, not that production readiness is fully verified. "
        "Use --strict to require every checklist gate to be PASS.",
        "",
    ])
    return "\n".join(lines)


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
    try:
        output = report_dict(args.root)
    except InvalidRepositoryRootError as exc:
        if args.json:
            print(json.dumps({
                "error": str(exc),
                "summary": {
                    "verification_state": "INVALID_ROOT",
                    "fully_verified": False,
                    "release_ready": False,
                },
            }, indent=2, sort_keys=True))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True) if args.json else render_markdown(output))
    summary = output["summary"]
    success = summary["fully_verified"] if args.strict else summary["no_known_code_blockers"]
    return 0 if success else 1


if __name__=="__main__":
    raise SystemExit(main())

__all__=["ReadinessItem","report_dict","render_markdown","run_readiness"]
