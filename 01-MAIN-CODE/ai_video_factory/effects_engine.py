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

    label_lower = label.lower()
    zoom_base = 1.04 + ((index % 4) * 0.01)
    source_w = max(target_w + 96, int(target_w * zoom_base))
    source_h = max(target_h + 96, int(target_h * zoom_base))
    vf = f"scale=w={source_w}:h={source_h}:force_original_aspect_ratio=increase"

    def should_apply(effect_name: str, default_threshold: float = 0.75) -> bool:
        if effect_name in filter_effectiveness:
            eff = filter_effectiveness[effect_name]
            effectiveness = eff.get("effectiveness", 0.5) if isinstance(eff, dict) else float(eff)
            return float(effectiveness) >= default_threshold
        return True

    has_motion = any(token in label_lower for token in ("camera move", "tracking", "reframe", "slow push"))
    has_shake = "subtle shake" in label_lower and should_apply("subtle_shake", 0.80)
    if has_motion:
        amplitude_x = max(6, target_w // 70)
        amplitude_y = max(6, target_h // 140)
        period = max(float(duration), 0.25)
        x_expr = f"max(0,min(iw-ow,(iw-ow)/2+{amplitude_x}*sin(2*PI*t/{period:.3f})))"
        y_expr = f"max(0,min(ih-oh,(ih-oh)/2+{amplitude_y}*cos(2*PI*t/{period:.3f})))"
        vf += f",crop=w={target_w}:h={target_h}:x='{x_expr}':y='{y_expr}'"
    elif has_shake:
        amplitude_x = max(2, target_w // 120)
        amplitude_y = max(2, target_h // 240)
        period = max(float(duration), 0.25)
        x_expr = f"max(0,min(iw-ow,(iw-ow)/2+{amplitude_x}*sin(2*PI*t/{period:.3f})))"
        y_expr = f"max(0,min(ih-oh,(ih-oh)/2+{amplitude_y}*cos(2*PI*t/{period:.3f})))"
        vf += f",crop=w={target_w}:h={target_h}:x='{x_expr}':y='{y_expr}'"
    else:
        vf += f",crop=w={target_w}:h={target_h}:x='(iw-ow)/2':y='(ih-oh)/2'"

    if ("jump cut" in label_lower or "impact frame" in label_lower) and should_apply("jump_cut", 0.85):
        vf += ",tblend=all_mode='lighten':all_opacity=0.30"
    if (
        "quick zoom" in label_lower
        or "punchy zoom" in label_lower
        or "punch-in" in label_lower
        or "micro-zoom" in label_lower
    ) and should_apply("zoom_effect"):
        zoom_w = max(target_w + 32, int(target_w * 1.08))
        zoom_h = max(target_h + 32, int(target_h * 1.08))
        vf += f",scale=w={zoom_w}:h={zoom_h}:force_original_aspect_ratio=increase"
        vf += f",crop=w={target_w}:h={target_h}:x='(iw-ow)/2':y='(ih-oh)/2'"
    if "motion blur" in label_lower and should_apply("motion_blur", 0.70):
        vf += ",tblend=all_mode='average':all_opacity=0.55"
    if "speed ramp" in label_lower and should_apply("speed_ramp", 0.82):
        vf += ",tblend=all_mode='add':all_opacity=0.18"
    if ("dissolve" in label_lower or "match cut" in label_lower or "j-cut" in label_lower) and should_apply("cinematic_transition", 0.80):
        vf += ",fade=t=in:st=0:d=0.08"
    if "cinematic transition" in label_lower and should_apply("cinematic_transition", 0.80):
        vf += ",fade=t=in:st=0:d=0.12"
    if ("soft settle" in label_lower or "subtle-parallax" in label_lower) and should_apply("eq_effect"):
        vf += ",eq=gamma=1.04"
    if "retention accent" in label_lower and should_apply("retention_accent", 0.60):
        vf += ",eq=brightness=0.035:contrast=1.035"
    if ("hook" in label_lower or "payoff" in label_lower) and should_apply("unsharp_effect"):
        vf += ",unsharp=3:3:0.5"
    if "Main event" in label and duration > 1.5 and should_apply("boxblur"):
        vf += ",boxblur=1:1"
    return vf
