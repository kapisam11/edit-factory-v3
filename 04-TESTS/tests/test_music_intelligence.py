from ai_video_factory import music_intelligence


def test_energy_at_empty_map_defaults_to_midpoint():
    assert music_intelligence.energy_at({}, 10.0) == 0.5


def test_beat_strength_prefers_nearby_beat():
    music_map = {"beats": [1.0, 2.0]}
    assert music_intelligence.beat_strength(music_map, 1.02, tolerance=0.1) > 0.7
    assert music_intelligence.beat_strength(music_map, 1.5, tolerance=0.1) == 0.0


def test_transition_score_combines_beat_and_energy():
    music_map = {"beats": [1.0], "energy": [0.9, 0.9], "energy_duration": 2.0}
    score = music_intelligence.transition_score(music_map, 1.0)
    assert 0.8 <= score <= 1.0


def test_save_and_analyze_missing_input(tmp_path, monkeypatch):
    path = tmp_path / "missing.json"
    music_map = {"version": 1, "duration": 3.0, "beats": [], "energy": []}
    assert music_intelligence.save_music_map(music_map, str(path)) == str(path)
    assert path.exists()
    monkeypatch.setattr(music_intelligence, "_duration", lambda _: 3.0)
    result = music_intelligence.analyze_music(str(path))
    assert result["duration"] == 3.0
