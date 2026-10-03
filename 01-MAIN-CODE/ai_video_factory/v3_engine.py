        )

    # The opening is an editorial anchor, not merely the first grid tick.
    first = clips[0]
    kind, reason, confidence = purpose_kind.get(first.purpose, ("zoom", "HOOK_ESTABLISHMENT", 0.76))
    add_event(first.start, kind, reason, confidence)

    # Only purpose labels with an explicit editorial meaning can create
    # retention enhancements. Generic clip boundaries do not.
    for clip in clips[1:]:
        mapped = purpose_kind.get(clip.purpose)
        if mapped is None:
            continue
        kind, reason, confidence = mapped
        add_event(clip.start, kind, reason, confidence)

    # Music is synchronized to an existing narrative payoff rather than used
    # to manufacture arbitrary events on a clock.
    payoff_candidates = [
        clip.start
        for clip in clips
        if clip.purpose in {"Climax", "Payoff", "Punchline", "Final impact"}
    ]
    if payoff_candidates:
        nearest = min(payoff_candidates, key=lambda t: abs(t - music.drop_time))
        if abs(nearest - music.drop_time) <= max(0.75, config.retention_interval * 0.5):
            add_event(nearest, "beat drop", "PAYOFF_ALIGNMENT", 0.96)
    elif abs(music.drop_time - config.target_seconds * 0.72) <= 1.0:
        add_event(music.drop_time, "beat drop", "MUSICAL_TRANSITION", 0.72)

    # Attention-gap handling is deliberately diagnostic, not generative.
    # A long gap does not create an artificial editorial effect. The renderer receives
    # only evidence-backed semantic anchors; QC reports long stale intervals separately.
    # A final impact is useful when the clip plan explicitly contains one.
    final_clip = clips[-1]
    if final_clip.purpose in {"Final impact", "Payoff", "Reaction"} and not any(
        abs(event.time - final_clip.start) < 0.35 for event in events
    ):
        add_event(
            final_clip.start,
            "text",
            "FINAL_IMPACT",
            0.86,
        )
    return sorted(events, key=lambda event: event.time)

def platform_variants(config: V3Config) -> Dict[str, Dict[str, Any]]:
    config.validate()
    return {key: dict(value) for key, value in PLATFORM_PROFILES.items()}

def automated_editorial_checks(core: CoreIdea, edit_type: EditType, hooks: Sequence[HookPack], clips: Sequence[ClipBeat], retention: Sequence[RetentionEvent], config: V3Config) -> QualityReport:
    durations = [clip.end - clip.start for clip in clips]
    overlays = [clip.text_overlay.lower() for clip in clips]
    checks = {
        "emotion_defined": bool(core.target_emotion),
        "single_edit_type_strategy": edit_type in EDIT_STRATEGIES,
        "hook_under_two_seconds": bool(clips and clips[0].end <= 2.2),
        "hook_context_gap": bool(hooks and hooks[0].text and core.watch_to_end_reason),