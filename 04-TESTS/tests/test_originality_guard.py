from __future__ import annotations

import json
from pathlib import Path

from ai_video_factory.originality_guard import compare_recent_packages


SCRIPT_VALORANT = (
    "The round looks lost until the defender saves one flash and changes the timing. "
    "Instead of swinging alone, she waits for the teammate to pressure the other lane. "
    "That forces the last opponent to turn away from the plant, opening a safe path onto site. "
    "The useful lesson is the delayed utility and coordinated timing, not raw aim."
)
SCRIPT_FORTNITE = (
    "The round looks lost until the defender saves one flash and changes the timing. "
    "Instead of swinging alone, she waits for the teammate to pressure the other lane. "
    "That forces the last opponent to turn away from the plant, opening a safe path onto site. "
    "The useful lesson is the delayed utility and coordinated timing, not raw aim."
)
DISTINCT_SCRIPT = (
    "Our series always starts with a quick minimap check before the round begins. "
    "This time the opposing team spends two smokes early, so I keep the flash for the retake. "
    "I hold the rotate rather than chasing damage, then use the remaining utility to help the entry. "
    "The point is to preserve a resource for the next decision, not to copy the previous round."
)


def _package(
    path: Path,
    *,
    script: str,
    game: str,
    source_hash: str = "",
    sequence: list[dict] | None = None,
) -> Path:
    path.mkdir(parents=True)
    (path / "script.txt").write_text(script, encoding="utf-8")
    (path / "gaming_profile.json").write_text(
        json.dumps({"enabled": True, "game_name": game, "creator_angle": "clutch"}),
        encoding="utf-8",
    )
    if source_hash:
        (path / "source_manifest.json").write_text(
            json.dumps({"source_sha256": source_hash}),
            encoding="utf-8",
        )
    if sequence is not None:
        (path / "clip_source_evidence.json").write_text(json.dumps(sequence), encoding="utf-8")
    return path


def _same_sequence() -> list[dict]:
    return [
        {
            "clip_index": index,
            "purpose": purpose,
            "status": "supported",
            "source_scene": f"scene-{index}",
            "source_start": start,
            "source_end": end,
        }
        for index, (purpose, start, end) in enumerate(
            [
                ("Hook", 4.0, 6.0),
                ("Context", 8.0, 10.0),
                ("Climax", 21.0, 24.0),
                ("Payoff", 25.0, 28.0),
            ],
            start=1,
        )
    ]


def test_game_name_swapped_copy_is_flagged_for_review(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    _package(output / "prior", script=SCRIPT_FORTNITE, game="Fortnite")
    current = output / "current"
    current.mkdir()

    report = compare_recent_packages(
        current,
        SCRIPT_VALORANT,
        current_topic="Valorant clutch",
        current_profile={"enabled": True, "game_name": "Valorant", "creator_angle": "clutch"},
    )

    assert report["publish_blocked"] is True
    assert report["status"] == "review_required"
    assert report["flagged_packages"] == ["prior"]
    assert report["comparisons"][0]["script_similarity"] >= 0.86


def test_shared_intro_does_not_flag_a_substantively_different_script(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    _package(output / "prior", script=SCRIPT_VALORANT, game="Valorant")
    current = output / "current"
    current.mkdir()

    report = compare_recent_packages(
        current,
        DISTINCT_SCRIPT,
        current_topic="Valorant utility retake",
        current_profile={"enabled": True, "game_name": "Valorant", "creator_angle": "strategy"},
    )

    assert report["publish_blocked"] is False
    assert report["flagged_packages"] == []
    assert report["comparisons"][0]["script_similarity"] < 0.86


def test_reused_same_source_sequence_is_flagged_even_with_new_commentary(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    sequence = _same_sequence()
    _package(
        output / "prior",
        script=SCRIPT_VALORANT,
        game="Valorant",
        source_hash="a" * 64,
        sequence=sequence,
    )
    current = output / "current"
    current.mkdir()
    (current / "source_manifest.json").write_text(
        json.dumps({"source_sha256": "a" * 64}),
        encoding="utf-8",
    )
    (current / "clip_source_evidence.json").write_text(json.dumps(sequence), encoding="utf-8")

    report = compare_recent_packages(
        current,
        "The smoke timing changes the approach, and the rotation creates a different decision. "
        "I explain why the player saves utility, covers the exposed lane, and helps the team plant "
        "rather than taking a low-value duel that risks losing the round.",
        current_topic="Valorant rotation analysis",
        current_profile={"enabled": True, "game_name": "Valorant", "creator_angle": "strategy"},
    )

    assert report["publish_blocked"] is True
    assert report["comparisons"][0]["reused_source_sequence"] is True
    assert report["comparisons"][0]["source_sequence_similarity"] == 1.0


def test_reviewed_recurring_series_can_pass_with_distinct_contribution_note(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    _package(output / "prior", script=SCRIPT_VALORANT, game="Valorant")
    current = output / "current"
    current.mkdir()

    report = compare_recent_packages(
        current,
        SCRIPT_VALORANT,
        current_profile={"enabled": True, "game_name": "Valorant", "creator_angle": "clutch"},
        human_review={
            "status": "approved",
            "notes": (
                "This is the recurring tactical breakdown series. originality-reviewed: "
                "This episode explains a different delayed-utility decision, the new teammate "
                "coordination, and a newly authored tactical lesson for this round."
            ),
        },
    )

    assert report["publish_blocked"] is False
    assert report["status"] == "approved_with_distinct_contribution"
    assert report["human_review_override"]["approved"] is True


def test_no_history_is_reported_as_insufficient_not_as_proof_of_originality(tmp_path: Path) -> None:
    current = tmp_path / "output" / "current"
    current.mkdir(parents=True)

    report = compare_recent_packages(current, SCRIPT_VALORANT)

    assert report["status"] == "insufficient_data"
    assert report["publish_blocked"] is False
    assert "not a guarantee of originality" in report["limitations"][-1]
