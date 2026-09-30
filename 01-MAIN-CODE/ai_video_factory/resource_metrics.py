"""Per-job CPU, memory and optional NVIDIA GPU resource measurements."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    psutil = None


def snapshot_resources() -> dict[str, Any]:
    result: dict[str, Any] = {
        "timestamp": time.time(),
        "pid": os.getpid(),
        "cpu_percent": None,
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
    start_rss = start.get("rss_bytes")
    end_rss = end.get("rss_bytes")
    summary: dict[str, Any] = {
        "started_at": start.get("timestamp"),
        "finished_at": end.get("timestamp"),
        "rss_start_bytes": start_rss,
        "rss_end_bytes": end_rss,
        "cpu_percent_start": start_cpu,
        "cpu_percent_end": end_cpu,
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
