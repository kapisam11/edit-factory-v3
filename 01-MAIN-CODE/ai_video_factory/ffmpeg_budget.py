"""Cross-process FFmpeg concurrency budget using short-lived SQLite leases."""
from __future__ import annotations

import contextlib
import os
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Iterator, Sequence, Any


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


MAX_FFMPEG_CONCURRENCY = _int_env("AIVF_MAX_CONCURRENT_FFMPEG", 2)
WAIT_SECONDS = _int_env("AIVF_FFMPEG_SLOT_WAIT_SECONDS", 120)
LEASE_SECONDS = _int_env("AIVF_FFMPEG_SLOT_LEASE_SECONDS", 7_200)


def _db_path() -> str:
    configured = os.environ.get("AIVF_FFMPEG_BUDGET_DB", "").strip()
    if configured:
        path = Path(configured)
    else:
        path = Path(os.environ.get("AIVF_STATE_DIR", ".")).resolve() / "ffmpeg_slots.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ffmpeg_leases("
        "token TEXT PRIMARY KEY, pid INTEGER NOT NULL, acquired REAL NOT NULL)"
    )
    return conn


@contextlib.contextmanager
def slot() -> Iterator[None]:
    deadline = time.monotonic() + WAIT_SECONDS
    token = uuid.uuid4().hex
    acquired = False
    while time.monotonic() < deadline:
        now = time.time()
        try:
            with _connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "DELETE FROM ffmpeg_leases WHERE acquired < ?",
                    (now - LEASE_SECONDS,),
                )
                count = conn.execute("SELECT COUNT(*) FROM ffmpeg_leases").fetchone()[0]
                if count < MAX_FFMPEG_CONCURRENCY:
                    conn.execute(
                        "INSERT INTO ffmpeg_leases(token,pid,acquired) VALUES(?,?,?)",
                        (token, os.getpid(), now),
                    )
                    conn.commit()
                    acquired = True
                    break
                conn.rollback()
        except sqlite3.OperationalError:
            pass
        time.sleep(0.1)
    if not acquired:
        raise RuntimeError(
            f"FFmpeg concurrency budget exhausted ({MAX_FFMPEG_CONCURRENCY} slots)"
        )
    try:
        yield
    finally:
        try:
            with _connect() as conn:
                conn.execute("DELETE FROM ffmpeg_leases WHERE token=?", (token,))
        except sqlite3.Error:
            pass


def run_ffmpeg_subprocess(command: Sequence[str], **kwargs: Any):
    """Run FFmpeg with bounded captured output and terminate its full process tree on timeout.

    The V3 visual sampler intentionally captures raw frames, so stdout has a
    separate configurable limit (64 MiB by default) for that data.
    """
    argv = [str(item) for item in command]
    if not argv or not argv[0]:
        raise ValueError("FFmpeg command cannot be empty")

    timeout_value = kwargs.pop("timeout", None)
    timeout = (
        _int_env("AIVF_FFMPEG_TIMEOUT_SECONDS", 3600)
        if timeout_value is None
        else float(timeout_value)
    )
    if timeout <= 0:
        raise ValueError("FFmpeg timeout must be positive")

    check = bool(kwargs.pop("check", False))
    capture_output = bool(kwargs.pop("capture_output", False))
    text_mode = kwargs.get("text", kwargs.get("universal_newlines", False))
    if capture_output and ("stdout" in kwargs or "stderr" in kwargs):
        raise ValueError("stdout and stderr arguments may not be used with capture_output")
    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    stdout_captured = capture_output or kwargs.get("stdout") == subprocess.PIPE
    stderr_captured = capture_output or kwargs.get("stderr") == subprocess.PIPE

    stdout_limit = 0
    stderr_limit = 0
    if stdout_captured:
        try:
            stdout_limit = int(
                os.environ.get("AIVF_FFMPEG_SUBPROCESS_CAPTURE_LIMIT_BYTES", str(64 * 1024**2))
            )
        except ValueError as exc:
            raise ValueError("AIVF_FFMPEG_SUBPROCESS_CAPTURE_LIMIT_BYTES must be an integer") from exc
        if not 1024 <= stdout_limit <= 256 * 1024**2:
            raise ValueError(
                "AIVF_FFMPEG_SUBPROCESS_CAPTURE_LIMIT_BYTES must be between 1024 and 268435456"
            )
    if stderr_captured:
        try:
            stderr_limit = int(
                os.environ.get("AIVF_FFMPEG_SUBPROCESS_STDERR_LIMIT_BYTES", str(1024**2))
            )
        except ValueError as exc:
            raise ValueError("AIVF_FFMPEG_SUBPROCESS_STDERR_LIMIT_BYTES must be an integer") from exc
        if not 1024 <= stderr_limit <= 8 * 1024**2:
            raise ValueError(
                "AIVF_FFMPEG_SUBPROCESS_STDERR_LIMIT_BYTES must be between 1024 and 8388608"
            )

    process: subprocess.Popen[Any] | None = None
    stdout_chunks: list[bytes] = []
    stdout_size = [0]
    stdout_overflow: list[str] = []
    stderr_tail: list[bytes] = []
    stderr_size = [0]
    stderr_reader_error: list[str] = []
    stdout_reader_error: list[str] = []
    threads: list[Any] = []

    def to_bytes(chunk: str | bytes) -> bytes:
        return chunk if isinstance(chunk, bytes) else chunk.encode("utf-8", errors="replace")

    def pump_stdout() -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                raw = to_bytes(chunk)
                available = stdout_limit - stdout_size[0]
                if len(raw) > available:
                    if available > 0:
                        stdout_chunks.append(raw[:available])
                        stdout_size[0] += available
                    stdout_overflow.append(
                        f"FFmpeg stdout exceeded configured capture limit of {stdout_limit} bytes"
                    )
                    from .production_guardrails import terminate_process_tree
                    terminate_process_tree(process, grace_seconds=0.5)
                    return
                stdout_chunks.append(raw)
                stdout_size[0] += len(raw)
        except (OSError, ValueError) as exc:
            stdout_reader_error.append(str(exc))
        finally:
            stream.close()

    def pump_stderr() -> None:
        stream = process.stderr
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                raw = to_bytes(chunk)
                if len(raw) > stderr_limit:
                    raw = raw[-stderr_limit:]
                stderr_tail.append(raw)
                stderr_size[0] += len(raw)
                while stderr_tail and stderr_size[0] > stderr_limit:
                    overflow = stderr_size[0] - stderr_limit
                    first = stderr_tail[0]
                    if overflow >= len(first):
                        stderr_tail.pop(0)
                        stderr_size[0] -= len(first)
                    else:
                        stderr_tail[0] = first[overflow:]
                        stderr_size[0] -= overflow
        except (OSError, ValueError) as exc:
            stderr_reader_error.append(str(exc))
        finally:
            stream.close()

    with slot():
        try:
            if os.name == "nt":
                kwargs.setdefault(
                    "creationflags", getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            else:
                kwargs.setdefault("start_new_session", True)
            process = subprocess.Popen(argv, **kwargs)
            if stdout_captured:
                thread = __import__("threading").Thread(
                    target=pump_stdout, name="aivf-ffmpeg-captured-stdout", daemon=True
                )
                threads.append(thread)
                thread.start()
            if stderr_captured:
                thread = __import__("threading").Thread(
                    target=pump_stderr, name="aivf-ffmpeg-captured-stderr", daemon=True
                )
                threads.append(thread)
                thread.start()
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                from .production_guardrails import terminate_process_tree
                terminate_process_tree(process, grace_seconds=1.0)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                for thread in threads:
                    thread.join(timeout=2)
                stdout_value = b"".join(stdout_chunks) if stdout_captured else None
                stderr_value = b"".join(stderr_tail) if stderr_captured else None
                if text_mode:
                    encoding = str(kwargs.get("encoding") or "utf-8")
                    errors = str(kwargs.get("errors") or "replace")
                    stdout_value = stdout_value.decode(encoding, errors=errors) if stdout_value is not None else None
                    stderr_value = stderr_value.decode(encoding, errors=errors) if stderr_value is not None else None
                raise subprocess.TimeoutExpired(
                    argv, timeout, output=stdout_value, stderr=stderr_value
                ) from exc

            for thread in threads:
                thread.join(timeout=2)
            stdout_raw = b"".join(stdout_chunks) if stdout_captured else None
            stderr_raw = b"".join(stderr_tail) if stderr_captured else None
            encoding = str(kwargs.get("encoding") or "utf-8")
            errors = str(kwargs.get("errors") or "replace")
            stdout_value = (
                stdout_raw.decode(encoding, errors=errors)
                if stdout_raw is not None and text_mode
                else stdout_raw
            )
            stderr_value = (
                stderr_raw.decode(encoding, errors=errors)
                if stderr_raw is not None and text_mode
                else stderr_raw
            )
            if stdout_overflow:
                raise RuntimeError(stdout_overflow[-1])
            if stdout_reader_error:
                raise RuntimeError(f"FFmpeg stdout capture failed: {stdout_reader_error[-1]}")
            if stderr_reader_error:
                raise RuntimeError(f"FFmpeg stderr capture failed: {stderr_reader_error[-1]}")
            completed = subprocess.CompletedProcess(
                argv, returncode, stdout=stdout_value, stderr=stderr_value
            )
            if check and returncode != 0:
                raise subprocess.CalledProcessError(
                    returncode, argv, output=stdout_value, stderr=stderr_value
                )
            return completed
        except BaseException:
            if process is not None and process.poll() is None:
                from .production_guardrails import terminate_process_tree
                terminate_process_tree(process, grace_seconds=1.0)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            raise


def wrap_run_ffmpeg(original):
    def guarded(*args, **kwargs):
        with slot():
            return original(*args, **kwargs)
    guarded.__name__ = getattr(original, "__name__", "run_ffmpeg")
    guarded.__doc__ = getattr(original, "__doc__", None)
    return guarded


__all__=["MAX_FFMPEG_CONCURRENCY","WAIT_SECONDS","LEASE_SECONDS","slot","run_ffmpeg_subprocess","wrap_run_ffmpeg"]
