"""Standalone diagnostics for CLI, dashboard and support bundles."""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any, Iterable

from .production_guardrails import atomic_write_json, diagnose_environment, free_disk_bytes

def diagnostics_report(*, required_tools: Iterable[str] = ('ffmpeg','ffprobe'), directories: Iterable[str | Path] = ()) -> dict[str, Any]:
    checks = diagnose_environment(required_tools=required_tools, required_paths=directories)
    directory_checks = []
    for raw in directories:
        path = Path(raw)
        writable = False
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / '.aivf-write-test'
            probe.write_text('ok', encoding='utf-8')
            probe.unlink(missing_ok=True)
            writable = True
        except OSError:
            writable = False
        try: free = free_disk_bytes(path)
        except OSError: free = 0
        directory_checks.append({'path': str(path), 'writable': writable, 'free_bytes': free})
    return {'ok': all(item.ok for item in checks) and all(item['writable'] for item in directory_checks),
            'platform': platform.platform(), 'python': platform.python_version(), 'pid': os.getpid(),
            'checks': [{'key': item.key, 'ok': item.ok, 'detail': item.detail} for item in checks],
            'directories': directory_checks}

def diagnostics_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)

def write_diagnostics(path: str | Path, report: dict[str, Any]) -> str:
    return atomic_write_json(path, report)

def summarize_failures(report: dict[str, Any]) -> list[str]:
    failures = [item['detail'] for item in report.get('checks', []) if not item.get('ok')]
    failures.extend(f"{item['path']}: not writable" for item in report.get('directories', []) if not item.get('writable'))
    return failures

__all__ = ['diagnostics_report','diagnostics_json','write_diagnostics','summarize_failures']