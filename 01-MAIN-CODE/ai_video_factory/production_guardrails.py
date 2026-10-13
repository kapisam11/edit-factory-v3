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
import threading
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

def atomic_write_json(path: str | Path, payload: Any) -> str:
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
$targets = [System.Collections.Generic.List[int]]::new()
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

def run_tool(
    argv: Sequence[str],
    *,
    timeout: int = 60,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stderr_limit: int = 4000,
    max_stdout_bytes: int = 8 * 1024 * 1024,
) -> ToolResult:
    """Run a command with a hard deadline, process-tree teardown, and bounded capture.

    stdout is capped because callers commonly parse tool output as JSON. stderr is
    drained continuously but only its tail is retained for diagnostics. A process
    that exceeds the stdout budget is terminated rather than allowed to exhaust RAM.
    """
    args = tuple(str(item) for item in argv)
    if not args or not args[0]:
        raise GuardrailError("tool command cannot be empty")
    if isinstance(timeout, bool) or int(timeout) <= 0:
        raise GuardrailError("tool timeout must be positive")
    stdout_limit = int(max_stdout_bytes)
    if stdout_limit < 1:
        raise GuardrailError("tool stdout limit must be positive")
    stderr_tail_limit = max(256, int(stderr_limit))

    try:
        process = subprocess.Popen(
            list(args),
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env is not None else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            **_process_group_kwargs(),
        )
    except OSError as exc:
        raise GuardrailError(f"could not start tool {args[0]}: {type(exc).__name__}") from exc

    assert process.stdout is not None
    assert process.stderr is not None
    stdout_overflow = threading.Event()
    stdout_capture: list[bytes] = []
    stderr_capture: list[bytes] = []

    def drain(
        stream: Any,
        destination: list[bytes],
        limit: int,
        *,
        keep_tail: bool,
        overflow: threading.Event | None = None,
    ) -> None:
        buffer = bytearray()
        try:
            while True:
                chunk = os.read(stream.fileno(), 64 * 1024)
                if not chunk:
                    break
                if overflow is not None and len(buffer) + len(chunk) > limit:
                    overflow.set()
                if keep_tail:
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        del buffer[: len(buffer) - limit]
                elif len(buffer) < limit:
                    buffer.extend(chunk[: limit - len(buffer)])
        except (OSError, ValueError):
            # The pipe can close while a killed process tree is being reaped.
            pass
        destination.append(bytes(buffer))

    stdout_thread = threading.Thread(
        target=drain,
        args=(process.stdout, stdout_capture, stdout_limit),
        kwargs={"keep_tail": False, "overflow": stdout_overflow},
        name=f"tool-stdout-{process.pid}",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=drain,
        args=(process.stderr, stderr_capture, stderr_tail_limit),
        kwargs={"keep_tail": True},
        name=f"tool-stderr-{process.pid}",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    deadline = time.monotonic() + int(timeout)
    timed_out = False
    overflowed = False
    while True:
        if stdout_overflow.is_set():
            overflowed = True
            terminate_process_tree(process)
            break
        if time.monotonic() >= deadline and (
            process.poll() is None or stdout_thread.is_alive() or stderr_thread.is_alive()
        ):
            # A grandchild can inherit the pipes and outlive a parent that
            # exits successfully. The deadline covers pipe EOF/reaping too.
            timed_out = True
            terminate_process_tree(process)
            break
        if (
            process.poll() is not None
            and not stdout_thread.is_alive()
            and not stderr_thread.is_alive()
        ):
            break
        time.sleep(0.02)

    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired as exc:
            raise GuardrailError(f"could not reap terminated tool process: {args[0]}") from exc

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    for pipe in (process.stdout, process.stderr):
        try:
            pipe.close()
        except OSError:
            pass
    stdout_thread.join(timeout=0.25)
    stderr_thread.join(timeout=0.25)

    if timed_out:
        raise GuardrailError(f"tool timed out after {int(timeout)}s: {args[0]}")
    if overflowed or stdout_overflow.is_set():
        raise GuardrailError(
            f"tool stdout exceeded the configured {stdout_limit}-byte limit: {args[0]}"
        )

    stdout = stdout_capture[0].decode("utf-8", errors="replace") if stdout_capture else ""
    stderr = stderr_capture[0].decode("utf-8", errors="replace") if stderr_capture else ""
    return ToolResult(args, int(process.returncode or 0), stdout, stderr.strip())

def redact_log_message(message: object, *, max_length: int = 4000) -> str:
    """Redact common credential-bearing values before they reach logs."""
    value = str(message or "")
    patterns = (
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"(?i)(\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+",
        r"(?i)(https?://)([^\s/@:]+):([^\s/@]+)@",
        r"(?i)([?&](?:access_token|api_key|token|secret|password)=)[^&\s]+",
    )
    for index, pattern in enumerate(patterns):
        if index == 2:
            value = re.sub(pattern, r"\1<REDACTED>:<REDACTED>@", value)
        else:
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
