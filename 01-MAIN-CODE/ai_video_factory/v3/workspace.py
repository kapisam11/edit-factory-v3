"""Cross-platform exclusive workspace locking for V3 jobs."""
from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
import json
import os
import time
from typing import BinaryIO


class WorkspaceBusyError(RuntimeError):
    """Raised when another process owns the workspace lock."""


class WorkspaceLock(AbstractContextManager["WorkspaceLock"]):
    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace)
        self.path = self.workspace / ".lock"
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            handle.write(b" ")
            handle.flush()
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                locking = getattr(msvcrt, "locking")
                mode = getattr(msvcrt, "LK_NBLCK")
                locking(handle.fileno(), mode, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise WorkspaceBusyError(f"workspace is already locked: {self.workspace}") from exc

        handle.seek(0)
        handle.truncate()
        payload = {
            "pid": os.getpid(),
            "started_at": time.time(),
            "workspace": str(self.workspace),
        }
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                locking = getattr(msvcrt, "locking")
                mode = getattr(msvcrt, "LK_UNLCK")
                locking(handle.fileno(), mode, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> "WorkspaceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


__all__ = ["WorkspaceBusyError", "WorkspaceLock"]
