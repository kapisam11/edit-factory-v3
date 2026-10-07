"""Resource classes and hard class-specific concurrency budgets."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JobClass(str, Enum):
    CPU_RENDER = "cpu_render"
    GPU_RENDER = "gpu_render"
    AI_HEAVY = "ai_heavy"
    LIGHT_QC = "light_qc"


@dataclass(frozen=True)
class JobClassBudget:
    max_active: int
    max_cpu_weight: float


DEFAULT_CLASS_BUDGETS = {
    JobClass.CPU_RENDER.value: JobClassBudget(max_active=2, max_cpu_weight=8.0),
    JobClass.GPU_RENDER.value: JobClassBudget(max_active=1, max_cpu_weight=4.0),
    JobClass.AI_HEAVY.value: JobClassBudget(max_active=1, max_cpu_weight=4.0),
    JobClass.LIGHT_QC.value: JobClassBudget(max_active=4, max_cpu_weight=4.0),
}


def classify_job_class(params: dict) -> str:
    if params.get("enable_diarization") or params.get("enable_object_detection") or params.get("enable_ocr"):
        return JobClass.AI_HEAVY.value
    if params.get("gpu_render") or params.get("encoder", "").lower() in {"h264_nvenc", "hevc_nvenc", "h264_vaapi", "h264_amf"}:
        return JobClass.GPU_RENDER.value
    workflow = str(params.get("workflow") or "").lower()
    if workflow in {"qc", "package_only"} and not params.get("raw_video"):
        return JobClass.LIGHT_QC.value
    return JobClass.CPU_RENDER.value


def budget_for(job_class: str) -> JobClassBudget:
    return DEFAULT_CLASS_BUDGETS.get(str(job_class), DEFAULT_CLASS_BUDGETS[JobClass.CPU_RENDER.value])


__all__ = ["JobClass", "JobClassBudget", "DEFAULT_CLASS_BUDGETS", "classify_job_class", "budget_for"]
