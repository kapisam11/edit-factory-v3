from __future__ import annotations

import json
from pathlib import Path

from ai_video_factory.originality_guard import compare_recent_packages


SCRIPT_VALORANT = (
    "In Valorant, the round looks lost until the defender saves one flash and changes the timing. "
    "Instead of swinging alone, she waits for the teammate to pressure the other lane. "
    "That forces the last opponent to turn away from the plant, opening a safe path onto site. "
    "The useful lesson is the delayed utility and coordinated timing, not raw aim."
)
SCRIPT_FORTNITE = (
    "In Fortnite, the round looks lost until the defender saves one flash and changes the timing. "
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
    assert "not a guarantee of originality" in report["reason"]



def test_upload_package_blocks_near_duplicate_without_explicit_originality_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_video_factory import upload_package

    output = tmp_path / "output"
    output.mkdir()
    _package(output / "prior", script=SCRIPT_FORTNITE, game="Fortnite")
    current = output / "current"
    current.mkdir()
    video = current / "final.mp4"
    video.write_bytes(b"fake-media; ffprobe is mocked")

    monkeypatch.setenv("AIVF_ENV", "test")
    monkeypatch.setenv("AIVF_REQUIRE_HUMAN_REVIEW", "0")
    monkeypatch.delenv("AIVF_ENABLE_AUDIO_SIGNAL_QC", raising=False)
    monkeypatch.setattr(
        upload_package,
        "media_rights_report",
        lambda _summary: {"status": "cleared", "publish_blocked": False, "sources": [], "unverified_sources": []},
    )
    monkeypatch.setattr(
        upload_package,
        "_probe_media",
        lambda _path: {
            "available": True,
            "format": {"duration": "30", "size": "100", "format_name": "mp4"},
            "streams": [
                {"codec_type": "video", "width": 1080, "height": 1920, "avg_frame_rate": "30/1", "duration": "30"},
                {"codec_type": "audio", "duration": "30"},
            ],
        },
    )
    monkeypatch.setattr(
        upload_package,
        "_media_contract_report",
        lambda *_args, **_kwargs: {
            "passed": True,
            "checks": {"ffprobe_available": True, "duration_valid": True},
            "errors": [],
            "probe": {"has_audio": True},
            "expected": {},
        },
    )
    monkeypatch.setattr(
        upload_package,
        "validate_metadata_quality",
        lambda *_args, **_kwargs: {"passed": True, "score": 100.0, "checks": {}, "issues": []},
    )
    monkeypatch.setattr(
        upload_package,
        "metadata_fact_gate",
        lambda *_args, **_kwargs: {"publish_blocked": False, "status": "cleared", "issues": []},
    )

    manifest = upload_package.finalize_upload_package(
        str(current),
        topic="Valorant clutch breakdown",
        summary={
            "gaming_profile": {
                "enabled": True,
                "game_name": "Valorant",
                "creator_angle": "clutch",
                "publish_blocked": False,
            },
        },
        script=SCRIPT_VALORANT,
        platform="youtube_shorts",
        final_video=str(video),
    )

    assert manifest["media_contract"]["passed"] is True
    assert manifest["media_rights"]["publish_blocked"] is False
    assert manifest["originality"]["publish_blocked"] is True
    assert manifest["publish_ready"] is False


def test_short_script_does_not_disable_repeat_sequence_check(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    sequence = _same_sequence()
    _package(
        output / "prior",
        script="One clutch moment.",
        game="Valorant",
        source_hash="b" * 64,
        sequence=sequence,
    )
    current = output / "current"
    current.mkdir()
    (current / "source_manifest.json").write_text(
        json.dumps({"source_sha256": "b" * 64}),
        encoding="utf-8",
    )
    (current / "clip_source_evidence.json").write_text(json.dumps(sequence), encoding="utf-8")

    report = compare_recent_packages(
        current,
        "Another short caption.",
        current_profile={"enabled": True, "game_name": "Valorant", "creator_angle": "clutch"},
    )

    assert report["comparable_script_count"] == 0
    assert report["comparable_source_sequence_count"] == 1
    assert report["publish_blocked"] is True
    assert report["comparisons"][0]["reused_source_sequence"] is True
