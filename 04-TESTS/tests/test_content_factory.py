from pathlib import Path

from ai_video_factory.content_factory import (
    CheckpointStore,
    adaptive_pacing,
    audio_first_timeline,
    build_word_level_captions,
    competitor_content_gap,
    dependency_fingerprint,
    detect_dead_time,
    detect_emotion,
    detect_visual_redundancy,
    disclosure_policy,
    duplicate_fingerprint,
    generate_chapters,
    generate_content_strategy,
    generate_hook_candidates,
    learn_channel_style,
    long_and_short_plan,
    performance_feedback,
    plan_source_aware_crop,
    queue_jobs,
    score_shots,
    thumbnail_factory_plan,
    validate_render,
)


def test_word_captions_audio_first_and_chapters():
    words = [
        {"word": "This", "start": 0.0, "end": 0.2},
        {"word": "is", "start": 0.2, "end": 0.4},
        {"word": "the", "start": 0.4, "end": 0.6},
        {"word": "TRUTH", "start": 0.6, "end": 0.9},
    ]
    cues = build_word_level_captions(words)
    assert cues
    assert "TRUTH" in cues[0]["emphasis"]
    assert audio_first_timeline(words)
    assert generate_chapters(cues, min_gap=0.0)


def test_shot_scoring_redundancy_and_pacing():
    ranked = score_shots([{"id": "a", "motion": 1, "faces": 1, "uniqueness": 1, "text": "minecraft win"}], "minecraft")
    assert ranked[0]["score"] > 0.5
    result = detect_visual_redundancy([
        {"id": "a", "phash": "10101010"},
        {"id": "b", "phash": "10101010"},
    ])
    assert result["duplicate_count"] == 1
    durations = adaptive_pacing([0.1, 0.9, 0.8], total_seconds=9)
    assert abs(sum(durations) - 9) < 0.05
    assert all(0.45 <= duration <= 4.0 for duration in durations)
    assert detect_dead_time([], []) == []


def test_strategy_hooks_emotion_and_output_planning(tmp_path):
    assert len(generate_content_strategy("Minecraft betrayal")) == 10
    assert generate_hook_candidates("Minecraft", {"main_conflict": "betrayal"})
    emotion = detect_emotion("This was an insane victory")
    assert emotion["scores"]["hype"] > emotion["scores"]["curious"]
    assert emotion["emotion"] == "hype"
    assert long_and_short_plan("Minecraft")["shared_assets"] is True
    assert plan_source_aware_crop(1920, 1080, __import__("ai_video_factory.content_factory", fromlist=["get_layout"]).get_layout("shorts"), [{"x": 0.7, "y": 0.5}])["crop"]["width"] > 0


def test_package_ops_feedback_learning_and_queue(tmp_path):
    assert competitor_content_gap("minecraft", candidates=[{"title": "football"}])["available"] is True
    assert thumbnail_factory_plan([{"id": "b", "contrast": 1, "clarity": 1}, {"id": "a", "contrast": 0, "clarity": 0}])[0]["id"] == "b"
    assert duplicate_fingerprint(script="hello", title="hello")["fingerprint"]
    assert disclosure_policy(ai_generated=True, realistic_alteration=False, platform="youtube")["review_required"]
    assert performance_feedback({"views": 100, "likes": 10})["engagement_rate"] > 0
    style = learn_channel_style([{"cuts_per_minute": 15, "avg_shot_duration": 2, "caption_style": "karaoke"}])
    assert style["video_count"] == 1
    queue_path = tmp_path / "queue.json"
    payload = queue_jobs(str(queue_path), ["one", "two"])
    assert len(payload["jobs"]) == 2
    store = CheckpointStore(str(tmp_path / "checkpoint.json"))
    store.mark("render", status="complete")
    assert store.is_complete("render")
    assert dependency_fingerprint({"a": 1}) == dependency_fingerprint({"a": 1})


def test_validate_render_missing_file(tmp_path):
    result = validate_render(str(Path(tmp_path) / "missing.mp4"))
    assert result["ok"] is False
