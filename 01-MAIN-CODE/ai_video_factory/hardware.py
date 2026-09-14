"""Hardware detection and encoder preset selection."""
import shutil
from typing import Any, Dict

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    psutil = None


def detect_nvidia() -> bool:
    """Return True if `nvidia-smi` is available on PATH."""
    return shutil.which("nvidia-smi") is not None


def detect_amd() -> bool:
    """Return True if AMD hardware is likely available."""
    return shutil.which("amf") is not None or shutil.which("rocm-smi") is not None


def detect_intel_gpu() -> bool:
    """Return True if Intel GPU tools are available."""
    return shutil.which("vainfo") is not None or shutil.which("intel_gpu_top") is not None


def get_memory_gb() -> float:
    """Estimate the system RAM in GB."""
    if psutil is None:
        return 0.0
    try:
        total = float(psutil.virtual_memory().total)
        return total / (1024 ** 3)
    except Exception:
        return 0.0


def choose_performance_profile() -> str:
    """Return 'best' or 'minimum' profile based on detected hardware."""
    if detect_nvidia() or detect_amd() or detect_intel_gpu():
        if get_memory_gb() >= 16.0:
            return "best"
    return "minimum"


def choose_encoder() -> str:
    """Choose an ffmpeg encoder string based on detected hardware.

    Returns one of: 'h264_nvenc', 'hevc_nvenc', 'libx264'.
    """
    if detect_nvidia():
        return "h264_nvenc"
    if detect_amd():
        return "hevc_nvenc"
    return "libx264"


def ffmpeg_preset_for(encoder: str) -> Dict[str, str]:
    """Return a small dict of recommended ffmpeg options for an encoder."""
    if encoder == "h264_nvenc":
        return {"codec": "h264_nvenc", "preset": "p5", "rc": "vbr_hq", "bitrate": "8000k"}
    if encoder == "hevc_nvenc":
        return {"codec": "hevc_nvenc", "preset": "p5", "rc": "vbr_hq", "bitrate": "8000k"}
    return {"codec": "libx264", "preset": "slow", "crf": "20"}
