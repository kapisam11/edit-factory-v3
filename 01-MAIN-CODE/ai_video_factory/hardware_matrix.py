"""Runtime encoder compatibility matrix and smoke testing."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .hardware import choose_encoder
from .render_engine import validate_media_output


ENCODERS = ("libx264", "h264_nvenc", "h264_amf", "h264_qsv", "h264_vaapi")


def ffmpeg_encoders() -> set[str]:
    binary = shutil.which("ffmpeg")
    if not binary:
        return set()
    result = subprocess.run([binary, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        return set()
    available = set()
    for line in result.stdout.splitlines():
        for encoder in ENCODERS:
            if encoder in line:
                available.add(encoder)
    return available


def run_encoder_smoke(encoder: str) -> dict[str, Any]:
    if encoder not in ENCODERS:
        raise ValueError(f"unsupported encoder: {encoder}")
    binary = shutil.which("ffmpeg")
    if not binary:
        return {"encoder": encoder, "available": False, "ok": False, "error": "ffmpeg missing"}
    with tempfile.TemporaryDirectory(prefix="aivf-encoder-") as tmp:
        output = Path(tmp) / "smoke.mp4"
        cmd = [
            binary, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
            "-t", "1", "-an", "-c:v", encoder,
        ]
        if encoder == "h264_nvenc":
            cmd += ["-preset", "p5", "-b:v", "2M"]
        elif encoder == "h264_amf":
            cmd += ["-quality", "speed", "-b:v", "2M"]
        elif encoder == "h264_qsv":
            cmd += ["-preset", "veryfast", "-b:v", "2M"]
        elif encoder == "h264_vaapi":
            return {"encoder": encoder, "available": encoder in ffmpeg_encoders(), "ok": False, "error": "VAAPI requires a host device; use target-host smoke"}
        cmd.append(str(output))
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        ok = result.returncode == 0 and output.is_file() and output.stat().st_size > 0
        error = ""
        if ok:
            try:
                validate_media_output(str(output), require_video=True, require_audio=False)
            except Exception as exc:
                ok = False
                error = f"output validation failed: {exc}"
        if not ok and not error:
            error = (result.stderr or "")[-1000:]
        return {"encoder": encoder, "available": encoder in ffmpeg_encoders(), "ok": ok, "error": error}


def run_matrix(encoders: Iterable[str] = ENCODERS) -> dict[str, Any]:
    detected = ffmpeg_encoders()
    results = []
    for encoder in encoders:
        if encoder not in detected:
            results.append({"encoder": encoder, "available": False, "ok": False, "error": "encoder unavailable"})
            continue
        results.append(run_encoder_smoke(encoder))
    return {
        "selected_encoder": choose_encoder(),
        "environment": {
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "rocm_visible_devices": os.environ.get("ROCR_VISIBLE_DEVICES", ""),
        },
        "results": results,
        "passed": sum(bool(item["ok"]) for item in results),
        "available": sum(bool(item["available"]) for item in results),
    }


__all__ = ["ENCODERS", "ffmpeg_encoders", "run_encoder_smoke", "run_matrix"]
