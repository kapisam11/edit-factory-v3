"""Cinematic video filter chain builder with effectiveness-aware selection."""
from typing import Dict, Any, Optional


def build_cinematic_filter(
    index: int,
    label: str,
    duration: float,
    filter_effectiveness: Optional[Dict[str, Any]] = None,
    target_size: tuple[int, int] = (1080, 1920),
) -> str:
    if filter_effectiveness is None:
        filter_effectiveness = {}
    if duration <= 0:
        raise ValueError("duration must be positive")

    target_w, target_h = target_size
    if target_w <= 0 or target_h <= 0:
        raise ValueError("target_size must be positive")
    zoom_base = 1.04 + ((index % 4) * 0.01)
    pattern = index % 3
    base_x = 0.5 + (0.01 * (pattern - 1))
    base_y = 0.5 + (0.01 * ((index % 2) - 0.5))

    # Always create an input larger than the output, then crop into the target.
    # This keeps every motion directive valid across portrait, square and landscape inputs.
    scale_w = max(target_w + 64, int(target_w * zoom_base))
    scale_h = max(target_h + 64, int(target_h * zoom_base))
    vf = (
        f"scale=w={scale_w}:h={scale_h}:force_original_aspect_ratio=increase,"
        f"crop=w={target_w}:h={target_h}:"
        f"x='max(0,min(iw-ow,(iw-ow)*{base_x:.4f}))':"
        f"y='max(0,min(ih-oh,(ih-oh)*{base_y:.4f}))',"
        f"eq=contrast=1.10:brightness=0.00:saturation=1.10"
    )
    label_lower = label.lower()

    def should_apply(effect_name: str, default_threshold: float = 0.75) -> bool:
        if effect_name in filter_effectiveness:
            eff = filter_effectiveness[effect_name]
            effectiveness = eff.get("effectiveness", 0.5) if isinstance(eff, dict) else float(eff)
            return float(effectiveness) >= default_threshold
        return True

    if ("jump cut" in label_lower or "impact frame" in label_lower) and should_apply("jump_cut", 0.85):
        vf += ",tblend=all_mode='lighten':all_opacity=0.30"
    if (
        "quick zoom" in label_lower
        or "punchy zoom" in label_lower
        or "punch-in" in label_lower
        or "micro-zoom" in label_lower
    ) and should_apply("zoom_effect"):
        vf += f",scale=w={int(target_w*1.08)}:h={int(target_h*1.08)}:force_original_aspect_ratio=increase"
        vf += f",crop=w={target_w}:h={target_h}:x='(iw-ow)/2':y='(ih-oh)/2'"
    if "motion blur" in label_lower and should_apply("motion_blur", 0.70):
        vf += ",tblend=all_mode='average':all_opacity=0.55"
    if "subtle shake" in label_lower and should_apply("subtle_shake", 0.80):
        amplitude_x = max(2, target_w // 120)
        amplitude_y = max(2, target_h // 240)
        vf += f",crop=w={target_w}:h={target_h}:x='max(0,min(iw-ow,(iw-ow)/2+{amplitude_x}*sin(2*PI*t/{duration:.3f})))':y='max(0,min(ih-oh,(ih-oh)/2+{amplitude_y}*cos(2*PI*t/{duration:.3f})))'"
    if "speed ramp" in label_lower and should_apply("speed_ramp", 0.82):
        vf += ",tblend=all_mode='add':all_opacity=0.18"
    if ("dissolve" in label_lower or "match cut" in label_lower or "j-cut" in label_lower) and should_apply("cinematic_transition", 0.80):
        vf += ",fade=t=in:st=0:d=0.08"
    if "cinematic transition" in label_lower and should_apply("cinematic_transition", 0.80):
        vf += ",fade=t=in:st=0:d=0.12"
    if ("soft settle" in label_lower or "subtle-parallax" in label_lower) and should_apply("eq_effect"):
        vf += ",eq=gamma=1.04"
    if (
        "camera move" in label_lower
        or "tracking" in label_lower
        or "reframe" in label_lower
        or "slow push" in label_lower
    ) and should_apply("pan_effect"):
        amplitude_x = max(4, target_w // 80)
        amplitude_y = max(4, target_h // 160)
        vf += f",crop=w={target_w}:h={target_h}:x='max(0,min(iw-ow,(iw-ow)/2+{amplitude_x}*sin(2*PI*t/{duration:.3f})))':y='max(0,min(ih-oh,(ih-oh)/2+{amplitude_y}*cos(2*PI*t/{duration:.3f})))'"
    if "retention accent" in label_lower and should_apply("retention_accent", 0.60):
        vf += ",eq=brightness=0.035:contrast=1.035"
    if ("hook" in label_lower or "payoff" in label_lower) and should_apply("unsharp_effect"):
        vf += ",unsharp=3:3:0.5"
    if "Main event" in label and duration > 1.5 and should_apply("boxblur"):
        vf += ",boxblur=1:1"
    return vf
