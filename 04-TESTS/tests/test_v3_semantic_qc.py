from types import SimpleNamespace

import ai_video_factory.v3_semantic_qc as qc


def _scene(scene_id, start, end, motion, audio, text=""):
    searchable = text or scene_id
    return SimpleNamespace(
        id=scene_id,
        start=start,
        end=end,
        motion_score=motion,
        audio_energy=audio,
        searchable_text=searchable,
    )


def test_isolated_short_dead_gap_is_warning(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(qc, "probe_media", lambda _path: {"duration": 8.0})
    monkeypatch.setattr(
        qc,
        "analyze_video",
        lambda *args, **kwargs: [
            _scene("a", 0.0, 2.0, 0.9, 0.9, "active"),
            _scene("b", 2.0, 3.0, 0.05, 0.02, "hold"),
            _scene("c", 3.0, 8.0, 0.8, 0.8, "payoff"),
        ],
    )

    report = qc.analyze_render_semantics(str(video))

    assert report["ok"] is True
    assert report["dead_gap_blocking"] is False
    assert report["dead_duration"] == 1.0
    assert "isolated low-motion/low-audio section detected" in report["warnings"]


def test_multiple_substantial_dead_gaps_still_block(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(qc, "probe_media", lambda _path: {"duration": 8.0})
    monkeypatch.setattr(
        qc,
        "analyze_video",
        lambda *args, **kwargs: [
            _scene("a", 0.0, 2.0, 0.9, 0.9, "active"),
            _scene("b", 2.0, 3.5, 0.05, 0.02, "hold one"),
            _scene("c", 3.5, 5.0, 0.8, 0.8, "active"),
            _scene("d", 5.0, 7.0, 0.05, 0.02, "hold two"),
            _scene("e", 7.0, 8.0, 0.9, 0.9, "payoff"),
        ],
    )

    report = qc.analyze_render_semantics(str(video))

    assert report["ok"] is False
    assert report["dead_gap_blocking"] is True
    assert "long low-motion/low-audio gaps detected" in report["errors"]
