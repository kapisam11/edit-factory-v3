"""Cinematic filter chain builder with effectiveness-aware selection."""
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

    target_w, target_h = target_size
    zoom_base = 1.04 + ((index % 4) * 0.01)
    pattern = index % 3
    if pattern == 0:
        x_crop = (index % 2) * 8
        y_crop = ((index + 1) % 3) * 3
    elif pattern == 1:
        x_crop = ((index + 1) % 2) * 12
        y_crop = ((index) % 3) * 4
    else:
        x_crop = ((index + 2) % 2) * 10
        y_crop = ((index + 2) % 3) * 3

    vf = (
        f"scale=iw*{zoom_base}:ih*{zoom_base},"
        f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black,"
        f"crop={target_w}:{target_h}:x={x_crop}:y={y_crop},"
        f"eq=contrast=1.10:brightness=0.00:saturation=1.10"
    )
    label_lower = label.lower()

    def should_apply(effect_name: str, default_threshold: float = 0.75) -> bool:
        if effect_name in filter_effectiveness:
            eff = filter_effectiveness[effect_name]
            effectiveness = eff.get("effectiveness", 0.5) if isinstance(eff, dict) else eff
            return effectiveness >= default_threshold
        return True

    if ("jump cut" in label_lower or "impact frame" in label_lower) and should_apply("jump_cut", 0.85):
        vf += ",tblend=all_mode='lighten':all_opacity=0.30"
    if ("quick zoom" in label_lower or "punchy zoom" in label_lower) and should_apply("zoom_effect"):
        vf += ",zoompan=z='if(lte(on,1),1.1,1.05)':d=1"
    if "motion blur" in label_lower and should_apply("motion_blur", 0.70):
        vf += ",tblend=all_mode='average':all_opacity=0.55"
    if "subtle shake" in label_lower and should_apply("subtle_shake", 0.80):
        vf += f",crop={target_w}:{target_h}:x='if(gt(mod(t,0.12),0.06),{x_crop+1},{x_crop})':y='if(gt(mod(t,0.12),0.06),{y_crop+1},{y_crop})'"
    if "speed ramp" in label_lower and should_apply("speed_ramp", 0.82):
        vf += ",tblend=all_mode='add':all_opacity=0.18"
    if "cinematic transition" in label_lower and should_apply("cinematic_transition", 0.80):
        vf += ",fade=t=in:st=0:d=0.12"
    if "soft settle" in label_lower and should_apply("eq_effect"):
        vf += ",eq=gamma=1.04"
    if ("camera move" in label_lower or "pan" in label_lower) and should_apply("pan_effect"):
        vf += ",pan=x='1920/2+(iw/2-1920/2)*if(lte(t,{:.1f}),t/{:.1f},1)':y='1080/2'".format(duration, duration)
    if ("hook" in label_lower or "payoff" in label_lower) and should_apply("unsharp_effect"):
        vf += ",unsharp=3:3:0.5"
    if "Main event" in label and duration > 1.5 and should_apply("boxblur"):
        vf += ",boxblur=1:1"
    return vf