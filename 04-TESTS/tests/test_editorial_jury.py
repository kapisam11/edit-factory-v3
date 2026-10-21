from __future__ import annotations

from ai_video_factory.editorial_jury import rank_clip_candidates, score_clip_candidate


def test_clip_jury_returns_named_bounded_dimensions_and_disclaims_prediction() -> None:
    result = score_clip_candidate(
        {
            "id": "scene-1",
            "start": 2.0,
            "end": 5.0,
            "description": "A player reveals the final clue and wins the match",
            "transcript": "And then he finally reveals the clue, and everyone reacts!",
            "importance_score": 0.9,
            "motion_score": 0.8,
            "audio_energy": 0.7,
            "face_count": 1,
        },
        "hook",
        topic="final clue match",
    )

    assert 0 <= result["overall"] <= 100
    assert set(result["dimensions"]) == {
        "hook_strength",
        "context_independence",
        "narrative_fit",
        "pacing",
        "information_density",
        "visual_energy",
        "source_support",
    }
    assert all(0 <= score <= 100 for score in result["dimensions"].values())
    assert abs(sum(result["weights"].values()) - 1.0) <= 0.001
    assert result["evaluator"] == "deterministic_editorial_heuristic"
    assert result["is_audience_prediction"] is False
    assert result["reasons"]


def test_ranker_sorts_candidates_and_caps_result_count() -> None:
    scenes = [
        {"id": "quiet", "start": 0, "end": 14, "description": "An empty room", "importance_score": 0.1},
        {"id": "reveal", "start": 4, "end": 7, "description": "The final reveal", "transcript": "Finally, here is the answer!", "importance_score": 0.95, "motion_score": 0.8, "audio_energy": 0.9},
    ]

    result = rank_clip_candidates(scenes, "hook", limit=1)

    assert len(result) == 1
    assert result[0]["scene_id"] == "reveal"
    assert result[0]["rank"] == 1


def test_purpose_specific_weights_are_normalized_for_payoff_and_hook() -> None:
    scene = {
        "start": 0.0,
        "end": 2.0,
        "description": "The final reveal solves the problem",
        "transcript": "Finally, the answer is revealed!",
        "importance_score": 1.0,
        "motion_score": 1.0,
        "audio_energy": 1.0,
        "face_count": 2,
    }
    for purpose in ("hook", "payoff", "climax", "punchline", "final impact"):
        result = score_clip_candidate(scene, purpose, topic="final reveal")
        assert abs(sum(result["weights"].values()) - 1.0) <= 0.001
        assert result["overall"] <= 100.0
