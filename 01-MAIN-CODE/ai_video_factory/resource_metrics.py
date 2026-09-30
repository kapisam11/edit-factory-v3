"""Per-job CPU, memory and optional NVIDIA GPU resource measurements."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any

try:
    import resource
except ImportError:  # pragma: no cover
    resource = None

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    psutil = None


def snapshot_resources() -> dict[str, Any]:
    result: dict[str, Any] = {
        "timestamp": time.time(),
        "pid": os.getpid(),
        "cpu_percent": None,
        "cpu_time_seconds": None,
        "rss_bytes": None,
        "vms_bytes": None,
        "gpu": None,
    }
    if psutil is not None:
        try:
            process = psutil.Process(os.getpid())
            result["cpu_percent"] = float(process.cpu_percent(interval=0.05))
            memory = process.memory_info()
            result["rss_bytes"] = int(memory.rss)
            result["vms_bytes"] = int(memory.vms)
        except (OSError, ValueError):
            pass

    try:
        cpu = os.times()
        result["cpu_time_seconds"] = round(float(cpu.user + cpu.system), 6)
    except (OSError, ValueError):
        pass
    if result["rss_bytes"] is None and resource is not None:
        try:
            raw_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            result["rss_bytes"] = raw_rss if sys.platform == "darwin" else raw_rss * 1024
        except (OSError, ValueError):
            pass

    if shutil.which("nvidia-smi"):
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if completed.returncode == 0:
                devices = []
                for line in (completed.stdout or "").splitlines():
                    parts = [item.strip() for item in line.split(",")]
                    if len(parts) != 3:
                        continue
                    try:
                        devices.append(
                            {
                                "utilization_percent": float(parts[0]),
                                "memory_used_mb": float(parts[1]),
                                "memory_total_mb": float(parts[2]),
                            }
                        )
                    except ValueError:
                        continue
                if devices:
                    result["gpu"] = devices
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def summarize_resources(start: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    start_cpu = start.get("cpu_percent")
    end_cpu = end.get("cpu_percent")
    start_cpu_time = start.get("cpu_time_seconds")
    end_cpu_time = end.get("cpu_time_seconds")
    start_rss = start.get("rss_bytes")
    end_rss = end.get("rss_bytes")
    summary: dict[str, Any] = {
        "started_at": start.get("timestamp"),
        "finished_at": end.get("timestamp"),
        "rss_start_bytes": start_rss,
        "rss_end_bytes": end_rss,
        "cpu_percent_start": start_cpu,
        "cpu_percent_end": end_cpu,
        "cpu_time_seconds_delta": (
            round(float(end_cpu_time) - float(start_cpu_time), 6)
            if start_cpu_time is not None and end_cpu_time is not None
            else None
        ),
        "elapsed_seconds": round(
            max(0.0, float(end.get("timestamp", 0.0)) - float(start.get("timestamp", 0.0))),
            3,
        ),
        "gpu_start": start.get("gpu"),
        "gpu_end": end.get("gpu"),
    }
    return summary


def resource_json(start: dict[str, Any], end: dict[str, Any]) -> str:
    return json.dumps(summarize_resources(start, end), sort_keys=True, indent=2)


__all__ = ["snapshot_resources", "summarize_resources", "resource_json"]


class ResourceMonitor:
    """Sample the worker process tree so short-lived FFmpeg children are included."""

    def __init__(self, interval_seconds: float = 1.0) -> None:
        import threading

        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._thread = None
        self.start = snapshot_resources()
        self.peak_rss_bytes = self.start.get("rss_bytes")
        self.peak_cpu_percent = self.start.get("cpu_percent")
        self.sample_count = 1
        self.child_processes_seen = 0

    def _tree_snapshot(self) -> dict[str, Any]:
        base = snapshot_resources()
        if psutil is None:
            return base
        try:
            root = psutil.Process(os.getpid())
            processes = [root, *root.children(recursive=True)]
        except (OSError, ValueError):
            return base
        total_rss = 0
        total_cpu = 0.0
        count = 0
        for process in processes:
            try:
                if process.is_running():
                    total_rss += int(process.memory_info().rss)
                    total_cpu += float(process.cpu_percent(interval=None))
                    count += 1
            except (OSError, ValueError):
                continue
        if count:
            base["rss_bytes"] = total_rss
            base["cpu_percent"] = total_cpu
            base["process_tree_count"] = count
            self.child_processes_seen = max(self.child_processes_seen, count - 1)
        return base

    def _run(self) -> None:
        # Prime cpu counters before sampling.
        self._tree_snapshot()
        while not self._stop.wait(self.interval_seconds):
            sample = self._tree_snapshot()
            self.sample_count += 1
            rss = sample.get("rss_bytes")
            cpu = sample.get("cpu_percent")
            if rss is not None:
                self.peak_rss_bytes = max(
                    int(self.peak_rss_bytes or 0), int(rss)
                )
            if cpu is not None:
                self.peak_cpu_percent = max(
                    float(self.peak_cpu_percent or 0.0), float(cpu)
                )

    def start_monitoring(self) -> None:
        import threading

        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="aivf-resource-monitor",
            daemon=True,
        )
        self._thread.start()

    def stop_monitoring(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_seconds + 1.0))
        end = self._tree_snapshot()
        return {
            **summarize_resources(self.start, end),
            "peak_rss_bytes": self.peak_rss_bytes,
            "peak_cpu_percent": self.peak_cpu_percent,
            "sample_count": self.sample_count,
            "child_processes_seen": self.child_processes_seen,
            "gpu_end": end.get("gpu"),
        }


__all__ = ["snapshot_resources", "summarize_resources", "resource_json", "ResourceMonitor"]
