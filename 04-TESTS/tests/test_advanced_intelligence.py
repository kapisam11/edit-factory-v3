from ai_video_factory.advanced_intelligence import (
    CaptionCue,
    WordTimestamp,
    build_choreographed_captions,
    choose_caption_position,
    music_aware_cut_plan,
)


def test_caption_position_avoids_face_region():
    x, y = choose_caption_position(face_boxes=[(0.35, 0.72, 0.65, 0.98)])
    assert y < 0.72


def test_choreographed_captions_split_on_speaker_and_word_limit():
    words = [
        WordTimestamp("one", 0.0, 0.2, "SPEAKER_00"),
        WordTimestamp("two", 0.2, 0.4, "SPEAKER_00"),
        WordTimestamp("three", 0.4, 0.6, "SPEAKER_01"),
    ]
    cues = build_choreographed_captions(words, max_words=5)
    assert len(cues) == 2
    assert cues[0].speaker == "SPEAKER_00"
    assert cues[1].speaker == "SPEAKER_01"


def test_music_aware_cut_plan_falls_back_without_beats():
    plan = music_aware_cut_plan([1.0, 2.0, 1.5], {"beats": []})
    assert plan == [(0.0, 1.0), (1.0, 3.0), (3.0, 4.5)]

 
def test_face_focus_track_rejects_invalid_parameters_before_opening_video():
    import pytest

    from ai_video_factory.advanced_intelligence import build_face_focus_track

    with pytest.raises(ValueError, match="start_time"):
        build_face_focus_track("missing.mp4", start_time=-1)
    with pytest.raises(ValueError, match="smoothing"):
        build_face_focus_track("missing.mp4", smoothing=0)
