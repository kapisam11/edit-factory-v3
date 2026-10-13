"""Evidence-driven 34-point production-readiness runner.

Behavioral entries run small, deterministic probes where practical. Repository
root checks are strict: an explicit checkout path is never silently replaced
by an installed package. Installed-wheel verification is an explicit mode.
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ReadinessItem:
    number: int
    name: str
    status: str
    evidence: str
    detail: str = ""


def _item(number: int, name: str, status: str, evidence: str, detail: str = "") -> ReadinessItem:
    return ReadinessItem(number, name, status, evidence, detail)


def validate_repository_root(root: str | Path) -> Path:
    """Validate an explicitly requested checkout instead of falling back elsewhere."""
    candidate = Path(root).expanduser()
    try:
        base = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Repository root does not exist or cannot be resolved: {candidate}") from exc
    if not base.is_dir():
        raise ValueError(f"Repository root is not a directory: {base}")
    package_dir = base / "01-MAIN-CODE" / "ai_video_factory"
    if not package_dir.is_dir():
        raise ValueError(
            f"Not an Edit Factory repository root: {base} "
            "(expected 01-MAIN-CODE/ai_video_factory)"
        )
    return base


def _required_code_checks(py: Path) -> list[tuple[str, Path]]:
    """Implementation-file checks, reported separately from behavior evidence."""
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
    """Report only file presence; never confuse it with a behavioral test."""
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


def _probe_path_containment() -> str:
    from .production_guardrails import GuardrailError, validate_path_inside

    with tempfile.TemporaryDirectory(prefix="aivf-ready-path-") as directory:
        root = Path(directory) / "assets"
        root.mkdir()
        escaped = Path(directory) / "outside.txt"
        try:
            validate_path_inside(root, escaped)
        except GuardrailError:
            return "Path traversal was rejected by validate_path_inside."
    raise RuntimeError("Path containment probe unexpectedly accepted a path outside the asset root")


def _probe_secret_safe_logging() -> str:
    from .production_guardrails import redact_log_message

    marker = "TEST_SECRET_12345"
    message = redact_log_message(
        f"Authorization: Bearer {marker}; access_token={marker}; password={marker}"
    )
    if marker in message:
        raise RuntimeError("test credential remains in the redacted diagnostic")
    return "Bearer, token, and password values were removed by the live redaction helper."


def _probe_scene_adapter() -> str:
    from .scene_detect_adapter import scene_windows

    with tempfile.TemporaryDirectory(prefix="aivf-ready-scene-") as directory:
        missing = Path(directory) / "missing-input.mp4"
        result = scene_windows(str(missing), 10.0)
    if result != []:
        raise RuntimeError("missing media did not produce the documented deterministic empty result")
    return "The scene adapter handled missing input without crashing."


def _probe_rights_gate() -> str:
    from .rights_policy import make_declaration, rights_gate

    missing = rights_gate([])
    if missing.get("publish_blocked") is not True:
        raise RuntimeError("missing rights evidence did not block publication")
    declaration = make_declaration(
        asset_id="readiness-probe",
        source="user_provided",
        rights_basis="owned",
        declared_by="readiness-check",
        now=datetime.now(timezone.utc),
    )
    accepted = rights_gate([declaration])
    if accepted.get("publish_blocked") is not False:
        raise RuntimeError("valid owned-source declaration was not accepted by the rights gate")
    return "Missing evidence was blocked and a valid owned-source declaration was accepted."


def _probe_factuality_guard() -> str:
    from .factuality_guard import metadata_fact_gate

    unsupported = metadata_fact_gate("Minecraft in 2026", "The event had 400 players.")
    reviewed = metadata_fact_gate(
        "Minecraft in 2026",
        "The event had 400 players.",
        evidence=[{"text": "The event had 400 players."}],
        fact_reviewed=True,
    )
    if unsupported.get("publish_blocked") is not True or reviewed.get("publish_blocked") is not False:
        raise RuntimeError("factuality gate did not distinguish unsupported from reviewed claims")
    return "Unsupported numerical claims were blocked; an evidenced and reviewed claim passed."


def _probe_human_review() -> str:
    from .human_review import create_review, review_gate

    if review_gate(None).get("publish_blocked") is not True:
        raise RuntimeError("missing human review did not block publication")
    review = create_review(
        "readiness-check",
        {
            "hook": 5,
            "pacing": 5,
            "visual_relevance": 5,
            "caption_readability": 5,
            "audio": 5,
            "rights": 5,
            "metadata": 5,
        },
        approved=True,
    )
    if review_gate(review).get("publish_blocked") is not False:
        raise RuntimeError("valid approved human review was not accepted")
    return "Missing review was blocked and a complete approved review passed."


def _probe_feedback_store() -> str:
    from .feedback_store import FeedbackStore

    with tempfile.TemporaryDirectory(prefix="aivf-ready-feedback-") as directory:
        store = FeedbackStore(Path(directory) / "feedback.db")
        store.add(
            "readiness-probe",
            "youtube",
            "readiness-probe",
            datetime.now(timezone.utc).isoformat(),
            {"retention": 0.5},
        )
        aggregate = store.aggregate("youtube")
    record = aggregate.get("youtube:readiness-probe", {})
    if record.get("samples") != 1 or record.get("means", {}).get("retention") != 0.5:
        raise RuntimeError("feedback could not be persisted and aggregated")
    return "A feedback record was persisted and recovered through the aggregate API."


def _probe_observability() -> str:
    from .observability_metrics import MetricsRegistry

    with tempfile.TemporaryDirectory(prefix="aivf-ready-metrics-") as directory:
        registry = MetricsRegistry(directory)
        registry.increment("readiness_probe_total")
        snapshot = registry.snapshot()
        exposition = registry.to_prometheus()
    if snapshot.counters.get("readiness_probe_total") != 1:
        raise RuntimeError("metric counter was not recorded")
    if "readiness_probe_total" not in exposition:
        raise RuntimeError("Prometheus exposition omitted the recorded metric")
    return "Counter storage and Prometheus exposition returned the recorded metric."


def _probe_durable_queue() -> str:
    from .job_backend import SQLiteJobBackend

    with tempfile.TemporaryDirectory(prefix="aivf-ready-queue-") as directory:
        backend = SQLiteJobBackend(Path(directory) / "queue.db", visibility_timeout=1.0)
        backend.enqueue("readiness-probe", {"topic": "readiness-probe"})
        claimed = backend.claim()
        if claimed is None:
            raise RuntimeError("queued item could not be claimed")
        if backend.complete(claimed.job_id, "stale-worker-token") is not False:
            raise RuntimeError("stale worker token was allowed to complete a job")
        if backend.complete(claimed.job_id, claimed.worker_token) is not True:
            raise RuntimeError("current worker token could not complete its job")
    return "A stale worker token was rejected and the current worker completed the job."


def _probe_rbac() -> str:
    from .authorization import Role, role_allows

    if role_allows(Role.VIEWER, Role.EDITOR):
        raise RuntimeError("viewer role unexpectedly satisfied editor permissions")
    if not role_allows(Role.ADMIN, Role.EDITOR):
        raise RuntimeError("admin role did not satisfy editor permissions")
    return "Viewer/editor and admin/editor permission boundaries behaved as expected."


def _probe_crash_recovery() -> str:
    from .job_recovery import reconcile_job

    interrupted: list[tuple[str, str]] = []
    recovered = reconcile_job(
        job_id="readiness-probe",
        pid=2_147_483_647,
        heartbeat_epoch=0.0,
        last_seen_epoch=0.0,
        timeout_seconds=1.0,
        mark_interrupted=lambda job_id, reason: interrupted.append((job_id, reason)),
    )
    if not recovered or len(interrupted) != 1 or interrupted[0][0] != "readiness-probe":
        raise RuntimeError("stale job with a dead worker was not reconciled")
    if "heartbeat expired" not in interrupted[0][1]:
        raise RuntimeError("recovery diagnostic did not explain why the job was interrupted")
    return "A stale job with a dead worker was marked interrupted with a diagnostic reason."


def _probe_cache_lifecycle() -> str:
    from .cache_lifecycle import CacheLifecycle

    with tempfile.TemporaryDirectory(prefix="aivf-ready-cache-") as directory:
        cache = CacheLifecycle(directory, max_bytes=10, ttl_seconds=60)
        (cache.root / "oversized.tmp").write_bytes(b"x" * 11)
        removed = cache.enforce_size()
    if removed != 1:
        raise RuntimeError(f"cache size enforcement removed {removed} entries; expected one")
    return "The cache lifecycle removed an oversized entry under a 10-byte cap."


def _evaluate_behavior_probe(name: str, evidence: str, probe: Callable[[], str]) -> tuple[str, str, str]:
    try:
        detail = probe()
    except Exception as exc:
        detail = f"{type(exc).__name__}: {str(exc).strip()[:240]}"
        return "BLOCKED", evidence, detail
    return "PASS", evidence, detail


def run_readiness(
    root: str | Path | None = ".",
    *,
    installed_package: bool = False,
) -> list[ReadinessItem]:
    """Run repository checks; installed-package checks must be opted into explicitly."""
    if installed_package:
        base = Path.cwd().resolve()
        py = Path(__file__).resolve().parent
    else:
        base = validate_repository_root(root if root is not None else ".")
        py = base / "01-MAIN-CODE" / "ai_video_factory"

    items: list[ReadinessItem] = []
    if installed_package:
        security_status = "ENVIRONMENT"
        security_evidence = "repository-wide security_audit.py"
        security_detail = "Installed-package mode cannot scan a source checkout; run this check from a validated repository root."
    else:
        try:
            from .security_audit import high_severity, scan_repository

            findings = scan_repository(base)
            high_findings = high_severity(findings)
            security_status = "PASS" if not high_findings else "BLOCKED"
            security_detail = f"{len(findings)} finding(s), {len(high_findings)} high severity"
        except Exception as exc:
            security_status = "BLOCKED"
            security_detail = f"{type(exc).__name__}: {str(exc).strip()[:240]}"
        security_evidence = "security_audit.scan_repository() and high_severity()"

    def probe_item(number: int, name: str, evidence: str, probe: Callable[[], str]) -> ReadinessItem:
        status, source, detail = _evaluate_behavior_probe(name, evidence, probe)
        return _item(number, name, status, source, detail)

    items.extend([
        _item(1, "Merge/process gate", "PROCESS", "working branch + pull request workflow", "Final merge still belongs to the repository owner."),
        _item(2, "CI verification", "CI", "GitHub Actions workflow state", "This runner does not infer CI success from local files."),
        _item(3, "Command-injection audit", security_status, security_evidence, security_detail),
        probe_item(4, "Repository path traversal", "live validate_path_inside() probe", _probe_path_containment),
        probe_item(5, "Secret-safe logging", "live redact_log_message() probe with TEST_SECRET_12345", _probe_secret_safe_logging),
        _item(6, "SLSA/build provenance", "CI", "release workflow attestation step", "Verify the release workflow run and published attestation."),
        probe_item(7, "PySceneDetect integration", "live scene_windows() missing-input probe", _probe_scene_adapter),
        _item(8, "Scene detection redesign", "CI", "scene-planning regression tests", "Algorithm quality and media fixtures are evaluated by the test workflow."),
        _item(9, "GPU/NVENC verification", "ENVIRONMENT", "runtime encoder check", "Requires the target machine and its installed GPU/driver stack."),
        _item(10, "Performance benchmark", "CI", "benchmark and performance regression tests", "Run under the same hardware/media profile used for production."),
        _item(11, "10+ GB media", "ENVIRONMENT", "large_media_smoke.py", "Run the opt-in large-file test on the target volume."),
        _item(12, "Exotic media compatibility", "ENVIRONMENT", "compatibility_matrix.py", "Run the target codecs/container matrix on the deployment host."),
        _item(13, "Audio normalization", "CI", "audio-normalization test group", "A readiness report must not substitute file presence for audio integration tests."),
        probe_item(14, "Rights/license verification", "live rights_gate() negative and positive probes", _probe_rights_gate),
        _item(15, "Content ID", "MANUAL", "external matching and rights review", "No local code probe can guarantee external Content ID outcomes."),
        probe_item(16, "Metadata factuality", "live metadata_fact_gate() probes", _probe_factuality_guard),
        _item(17, "Thumbnail quality", "CI", "thumbnail-quality regression tests", "Objective checks do not replace human review of creative quality."),
        probe_item(18, "Human-quality review", "live review_gate() negative and positive probes", _probe_human_review),
        probe_item(19, "Learning feedback loop", "live FeedbackStore persistence/aggregate probe", _probe_feedback_store),
        probe_item(20, "Observability", "live MetricsRegistry counter/Prometheus probe", _probe_observability),
        probe_item(21, "Durable queue / scaling boundary", "live SQLiteJobBackend stale-token fencing probe", _probe_durable_queue),
        probe_item(22, "Multi-user RBAC", "live role_allows() viewer/editor/admin probes", _probe_rbac),
        probe_item(23, "Crash recovery", "live reconcile_job() stale/dead worker probe", _probe_crash_recovery),
        probe_item(24, "Cache lifecycle", "live CacheLifecycle size-enforcement probe", _probe_cache_lifecycle),
        _item(25, "UI production surface", "CI", "authenticated dashboard/API end-to-end tests", "Verify job data, artifact download, retry, configuration, and publish surfaces in CI."),
        _item(26, "UI automation", "CI", "dashboard E2E workflow", "Check the latest successful end-to-end workflow run."),
        _item(27, "Typing boundary", "CI", "mypy hardened production boundary", "Check the current CI run instead of assuming typing passed."),
        _item(28, "Lint boundary", "CI", "Ruff correctness gate", "Check the current CI run instead of assuming lint passed."),
        _item(29, "Dependency refresh", "CI", "locked environment and dependency audit", "Check the current CI run instead of assuming dependencies are safe."),
        _item(30, "Reproducible build", "CI", "reproducible wheel workflow", "Check the current CI result."),
        _item(31, "OS/hardware matrix", "ENVIRONMENT", "Windows/macOS/Linux plus target-hardware checks", "CI cannot reproduce every target machine or GPU."),
        _item(32, "Real end-to-end video", "CI", "dashboard-e2e workflow renders a real media fixture", "Check the workflow result and inspect a representative output locally."),
        _item(33, "Before/after quality comparison", "MANUAL", "reference artifacts and benchmark comparison", "Requires reference artifacts and project-specific human interpretation."),
    ])

    code_check_results = _evaluate_code_checks(_required_code_checks(py))
    missing_code_checks = [check for check in code_check_results if check["status"] == "BLOCKED"]
    failed_probes = [item for item in items if item.status == "BLOCKED"]
    item_34_detail = (
        "Missing required implementation files: "
        + ", ".join(check["name"] for check in missing_code_checks)
        if missing_code_checks
        else "Required runtime evidence probes: "
        + ", ".join(item.name for item in failed_probes)
        if failed_probes
        else "Code evidence probes passed; CI, host, human, and external gates must still be satisfied."
    )
    item_34_status = "BLOCKED" if missing_code_checks or failed_probes else "MANUAL"
    items.append(
        _item(
            34,
            "10/10 evidence",
            item_34_status,
            "live behavior probes + separate required-file evidence",
            item_34_detail,
        )
    )
    return items


def _package_directory(root: str | Path | None = ".", *, installed_package: bool = False) -> Path:
    """Resolve the package being inspected without silently switching targets."""
    if installed_package:
        return Path(__file__).resolve().parent
    base = validate_repository_root(root if root is not None else ".")
    return base / "01-MAIN-CODE" / "ai_video_factory"


def report_dict(root: str | Path | None = ".", *, installed_package: bool = False) -> dict:
    items = run_readiness(None, installed_package=True) if installed_package else run_readiness(root)
    package_dir = _package_directory(root, installed_package=installed_package)
    code_checks = _evaluate_code_checks(_required_code_checks(package_dir))
    code_blockers = sum(check["status"] == "BLOCKED" for check in code_checks)
    checklist_blockers = sum(item.status == "BLOCKED" for item in items)
    no_known_code_blockers = code_blockers == 0 and checklist_blockers == 0
    fully_verified = no_known_code_blockers and all(item.status == "PASS" for item in items)
    outstanding_gates = sorted({item.status for item in items if item.status != "PASS"})
    verification_state = (
        "BLOCKED" if not no_known_code_blockers
        else "FULLY_VERIFIED" if fully_verified
        else "NOT_FULLY_VERIFIED"
    )
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


def render_markdown(data: dict) -> str:
    lines = [
        "# Edit Factory v3 — 34-point production readiness",
        "",
        "| # | Item | Status | Evidence | Detail |",
        "|---:|---|---|---|---|",
    ]
    for item in data["items"]:
        lines.append(
            f"| {item['number']} | {item['name']} | **{item['status']}** | "
            f"{item['evidence']} | {item.get('detail', '')} |"
        )
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
            f"| {check['name']} | **{check['status']}** | {check['path']} | {check['detail']} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "PASS means the named behavior probe actually ran and produced its expected result, "
        "or (only in the separate code-check table) the required file exists. CI means a "
        "repository workflow must be green. ENVIRONMENT means the deployment host must perform "
        "the check. MANUAL means a human or external service is required. PROCESS means the "
        "repository owner controls the final action. Installed-package mode cannot verify "
        "repository-wide checks.",
        "A normal exit code means there are no known code blockers, not that production readiness "
        "is fully verified. Use --strict to require every checklist gate to be PASS.",
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect Edit Factory v3 production-readiness evidence."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="repository root (default: current directory)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--installed-package",
        action="store_true",
        help="explicitly inspect the installed package instead of a source checkout",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return failure unless every checklist gate is PASS and release readiness is fully verified",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.installed_package and args.root is not None:
        print("ERROR: pass either a repository root or --installed-package, not both.", file=sys.stderr)
        return 2
    try:
        output = (
            report_dict(None, installed_package=True)
            if args.installed_package
            else report_dict(args.root or ".")
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True) if args.json else render_markdown(output))
    summary = output["summary"]
    success = summary["fully_verified"] if args.strict else summary["no_known_code_blockers"]
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ReadinessItem", "report_dict", "render_markdown", "run_readiness", "validate_repository_root"]
