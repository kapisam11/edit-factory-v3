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

 
def test_face_focus_track_smooths_normalized_face_centers(monkeypatch):
    import sys
    from types import SimpleNamespace

    import ai_video_factory.advanced_intelligence as intelligence

    class FakeCapture:
        released = False

        def isOpened(self):
            return True

        def get(self, prop):
            if prop == 1:  # CAP_PROP_FPS
                return 2.0
            if prop == 2:  # CAP_PROP_FRAME_COUNT
                return 4.0
            return 0.0

        def set(self, _prop, _value):
            return True

        def read(self):
            return True, SimpleNamespace(shape=(100, 100, 3))

        def release(self):
            self.released = True

    capture = FakeCapture()

    class FakeCascade:
        def empty(self):
            return False

        def detectMultiScale(self, _gray, **_kwargs):
            return [(60, 40, 20, 20)]

    fake_cv2 = SimpleNamespace(
        VideoCapture=lambda _path: capture,
        CAP_PROP_FPS=1,
        CAP_PROP_FRAME_COUNT=2,
        CAP_PROP_POS_MSEC=3,
        COLOR_BGR2GRAY=4,
        cvtColor=lambda frame, _code: frame,
        data=SimpleNamespace(haarcascades="fake-cascade-dir"),
        CascadeClassifier=lambda _path: FakeCascade(),
    )
    monkeypatch.setattr(intelligence, "require", lambda _capability: None)
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    track = intelligence.build_face_focus_track(
        "fake.mp4", duration=1.0, sample_seconds=0.5, smoothing=0.5
    )

    assert len(track) == 3
    assert track[0]["center_x"] == 0.7
    assert track[0]["center_y"] == 0.5
    assert all(point["confidence"] > 0 for point in track)
    assert capture.released
