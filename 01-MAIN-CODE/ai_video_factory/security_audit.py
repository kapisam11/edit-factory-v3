"""AST-based security audit for external-process and shell execution hazards."""
from __future__ import annotations
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

@dataclass(frozen=True)
class SecurityFinding:
    path: str
    line: int
    severity: str
    rule: str
    detail: str

def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        parts = []
        current = fn
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return ""

def _keyword_bool(node: ast.Call, name: str) -> bool | None:
    for kw in node.keywords:
        if kw.arg == name:
            if isinstance(kw.value, ast.Constant):
                return bool(kw.value.value)
            return None
    return False

def audit_source(path: str | Path) -> list[SecurityFinding]:
    source_path = Path(path)
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"), filename=str(source_path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        return [SecurityFinding(str(source_path), 0, "HIGH", "parse-error", str(exc))]
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in {"os.system", "os.popen"}:
            findings.append(SecurityFinding(str(source_path), node.lineno, "HIGH", "shell-execution", f"{name} must not be used"))
            continue
        if name.startswith("subprocess."):
            shell = _keyword_bool(node, "shell")
            if shell is True:
                findings.append(SecurityFinding(str(source_path), node.lineno, "HIGH", "subprocess-shell", "subprocess call enables a shell"))
            elif shell is None:
                findings.append(SecurityFinding(str(source_path), node.lineno, "HIGH", "dynamic-subprocess-shell", "subprocess shell mode is not statically known; fail closed"))
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                findings.append(SecurityFinding(
                    str(source_path), node.lineno, "MEDIUM", "string-command",
                    "subprocess command is a single string; prefer an explicit argv sequence",
                ))
    return findings

def scan_repository(root: str | Path, *, include_side_code: bool = True) -> list[SecurityFinding]:
    base = Path(root)
    roots = [base / "01-MAIN-CODE", base / "02-WEB-FILES"]
    if include_side_code:
        roots.append(base / "03-SIDE-CODE")
    findings = []
    for code_root in roots:
        if not code_root.is_dir():
            continue
        for path in sorted(code_root.rglob("*.py")):
            findings.extend(audit_source(path))
    return findings

def high_severity(findings: Iterable[SecurityFinding]) -> list[SecurityFinding]:
    return [item for item in findings if item.severity == "HIGH"]

def render_report(findings: Iterable[SecurityFinding]) -> str:
    rows = list(findings)
    lines = ["# Edit Factory Security Audit", "", f"Findings: {len(rows)}", ""]
    lines.append("| Severity | File | Line | Rule | Detail |")
    lines.append("|---|---|---:|---|---|")
    for item in rows:
        lines.append(f"| {item.severity} | {item.path} | {item.line} | {item.rule} | {item.detail} |")
    if not rows:
        lines.append("No security findings were detected.")
    return "\n".join(lines) + "\n"

def assert_clean(root: str | Path) -> None:
    findings = scan_repository(root)
    severe = high_severity(findings)
    if severe:
        details = "; ".join(f"{item.path}:{item.line} {item.rule}" for item in severe)
        raise RuntimeError(f"security audit failed: {details}")

def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    root = Path(args[0] if args else ".").resolve()
    findings = scan_repository(root)
    print(render_report(findings))
    return 1 if high_severity(findings) else 0

if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["SecurityFinding", "assert_clean", "audit_source", "high_severity", "render_report", "scan_repository"]
