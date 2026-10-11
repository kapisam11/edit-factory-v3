"""Cross-platform single-process lock for the native Windows publishing worker."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


class PublisherProcessLock:
    """Acquire an O_EXCL lock file; stale processes are recovered conservatively."""
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.token = uuid.uuid4().hex
        self.held = False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            # Access-denied/unknown process state must fail closed, not steal a lock.
            return True

    def __enter__(self) -> "PublisherProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                try:
                    raw = self.path.read_text(encoding="utf-8")
                    payload = json.loads(raw)
                    pid = int(payload.get("pid") or 0)
                    token = str(payload.get("token") or "")
                except (OSError, ValueError, TypeError):
                    # Don't remove a recently created/partially written lock.
                    try:
                        age = time.time() - self.path.stat().st_mtime
                    except OSError:
                        age = 0
                    if age < 60:
                        raise RuntimeError("another publisher instance may be starting; refusing to steal its lock")
                    pid, token = 0, ""
                if pid and self._pid_alive(pid):
                    raise RuntimeError(f"publisher is already running under process ID {pid}")
                # Re-read immediately before removal to avoid deleting a replacement lock.
                try:
                    current = json.loads(self.path.read_text(encoding="utf-8"))
                    if str(current.get("token") or "") != token:
                        continue
                    self.path.unlink()
                except (OSError, ValueError, TypeError):
                    continue
                continue
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump({"pid": os.getpid(), "token": self.token, "created_at": time.time()}, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                self.held = True
                return self
        raise RuntimeError("could not acquire the publisher process lock after stale-lock recovery")

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self.held:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass
        finally:
            self.held = False
