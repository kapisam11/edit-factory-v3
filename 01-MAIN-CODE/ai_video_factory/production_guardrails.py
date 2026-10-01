"""Small, dependency-light production guardrails used by Edit Factory."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

class GuardrailError(RuntimeError):
    """Raised when a production precondition cannot be satisfied."""

@dataclass(frozen=True)
class ToolResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr_tail: str

def validate_path_inside(base: str | Path, candidate: str | Path, *, allow_base: bool = False) -> Path:
    root = Path(base).expanduser().resolve(strict=False)
    target = Path(candidate).expanduser().resolve(strict=False)
    if target == root and allow_base:
        return target
    if root not in target.parents:
        raise GuardrailError(f"path escapes allowed root: {candidate}")
    return target

def safe_filename(value: str, *, fallback: str = "asset", max_length: int = 120) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isprintable()).strip()
    text = text.replace("/", "_").replace("\\", "_").replace(":", "_").replace("..", "_")
    text = "_".join(text.split()).strip(" .")
    return (text or fallback)[:max(1, int(max_length))]

def atomic_write_bytes(path: str | Path, payload: bytes) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".aivf-", suffix=".partial", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        try: Path(tmp).unlink(missing_ok=True)
        except OSError: pass
        raise
    return str(target)

def atomic_write_json(path: str | Path, payload: Mapping[str, Any]) -> str:
    data = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    return atomic_write_bytes(path, data)

def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(max(4096, int(chunk_size)))
            if not chunk: break
            digest.update(chunk)
    return digest.hexdigest()

def free_disk_bytes(path: str | Path) -> int:
    return int(shutil.disk_usage(Path(path)).free)

def require_free_disk(path: str | Path, minimum_bytes: int) -> None:
    free = free_disk_bytes(path)
    if free < int(minimum_bytes):
        raise GuardrailError(f"insufficient free disk space: {free} bytes available")

def executable_path(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise GuardrailError(f"required executable not found on PATH: {name}")
    return resolved

def _process_group_kwargs() -> dict[str, Any]:
    """Create an isolated process group/session for external tools."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}

def _windows_kill_surviving_descendants(pid: int, *, timeout_seconds: float = 3.0) -> None:
    """Kill descendants by parent-PID lineage when the original process already exited."""
    if os.name != "nt":
        return
    # Use PowerShell's CIM process table as a fallback after taskkill. This is
    # executed without a shell and the only interpolated value is an integer PID.
    script = r"""
param([int]$RootPid)
$all = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId)
$seen = @{}
$targets = New-Object System.Collections.Generic.List[int]
function Add-Descendants([int]$ParentPid) {
    foreach ($proc in $all) {
        $childPid = [int]$proc.ProcessId
        if ([int]$proc.ParentProcessId -eq $ParentPid -and -not $seen.ContainsKey($childPid)) {
            $seen[$childPid] = $true
            $targets.Add($childPid)
            Add-Descendants $childPid
        }
    }
}
Add-Descendants $RootPid
foreach ($childPid in $targets) {
    Stop-Process -Id $childPid -Force -ErrorAction SilentlyContinue
}
Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
"""
    try:
        subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script, str(int(pid))],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1.0, float(timeout_seconds)),
        )
    except (OSError, subprocess.SubprocessError):
        pass


def terminate_process_tree(process: subprocess.Popen[Any], *, grace_seconds: float = 2.0) -> None:
    """Terminate an external process group/tree, including exited-parent Windows descendants."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(1.0, float(grace_seconds) + 1.0),
            )
            _windows_kill_surviving_descendants(
                process.pid,
                timeout_seconds=max(1.0, float(grace_seconds) + 1.0),
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)
            deadline = time.monotonic() + max(0.1, float(grace_seconds))
            while time.monotonic() < deadline:
                try:
                    os.killpg(process.pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass

def run_tool(argv: Sequence[str], *, timeout: int = 60, cwd: str | Path | None = None, env: Mapping[str, str] | None = None, stderr_limit: int = 4000) -> ToolResult:
    """Run an external tool without a shell and retain a bounded diagnostic tail."""
    args = tuple(str(item) for item in argv)
    if not args or not args[0]:
        raise GuardrailError("tool command cannot be empty")
    if int(timeout) <= 0:
        raise GuardrailError("tool timeout must be positive")
    limit = max(256, int(stderr_limit))
    try:
        process = subprocess.Popen(list(args), cwd=str(cwd) if cwd else None, env=dict(env) if env is not None else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **_process_group_kwargs())
        try:
            stdout, stderr = process.communicate(timeout=int(timeout))
        except subprocess.TimeoutExpired as exc:
            terminate_process_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=1)
            raise GuardrailError(f"tool timed out after {timeout}s: {args[0]}") from exc
        completed = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(process)
        raise GuardrailError(f"tool timed out after {timeout}s: {args[0]}") from exc
    except OSError as exc:
        raise GuardrailError(f"could not start tool {args[0]}: {exc}") from exc
    stderr = (completed.stderr or "").strip()
    return ToolResult(args, completed.returncode, completed.stdout or "", stderr[-limit:])

def redact_log_message(message: object, *, max_length: int = 4000) -> str:
    """Redact common credential-bearing values before they reach logs."""
    value = str(message or "")
    patterns = (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"(?i)(\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+")
    for pattern in patterns:
        value = re.sub(pattern, r"\1<REDACTED>", value)
    return value[:max(256, int(max_length))]

class SecretRedactionFilter(logging.Filter):
    """Logging filter for accidental credential leakage."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_log_message(record.getMessage())
        record.args = ()
        return True

@dataclass(frozen=True)
class Diagnostic:
    key: str
    ok: bool
    detail: str

def diagnose_environment(*, required_tools: Iterable[str] = ("ffmpeg", "ffprobe"), required_paths: Iterable[str | Path] = ()) -> list[Diagnostic]:
    results = [Diagnostic("python", sys.version_info >= (3, 10), ".".join(map(str, sys.version_info[:3])))]
    for tool in required_tools:
        try: path = executable_path(tool)
        except GuardrailError as exc: results.append(Diagnostic(f"tool:{tool}", False, str(exc)))
        else: results.append(Diagnostic(f"tool:{tool}", True, path))
    for raw_path in required_paths:
        required_path = Path(raw_path)
        results.append(Diagnostic(f"path:{required_path}", required_path.exists(), "exists" if required_path.exists() else "missing"))
    return results

def redact_mapping(payload: Mapping[str, Any], *, secret_names: Iterable[str] = ()) -> dict[str, Any]:
    """Recursively redact secret-like keys in mappings and nested sequences."""
    secrets = {str(name).lower() for name in secret_names}
    markers = ("token", "secret", "password", "api_key", "authorization")
    def clean(value: Any, depth: int) -> Any:
        if depth > 8:
            return "[TRUNCATED]"
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                normalized = str(key).lower()
                if normalized in secrets or any(marker in normalized for marker in markers):
                    result[str(key)] = "[REDACTED]"
                else:
                    result[str(key)] = clean(item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            return [clean(item, depth + 1) for item in value]
        return value
    result = clean(payload, 0)
    return dict(result)

__all__ = ["Diagnostic","GuardrailError","ToolResult","atomic_write_bytes","atomic_write_json","diagnose_environment","executable_path","free_disk_bytes","redact_log_message","SecretRedactionFilter","redact_mapping","require_free_disk","run_tool","safe_filename","sha256_file","terminate_process_tree","validate_path_inside"]
