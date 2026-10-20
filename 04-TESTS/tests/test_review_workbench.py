from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_video_factory.review_workbench import load_review, restore_review, save_review, validate_srt


SRT = "1\n00:00:00,000 --> 00:00:01,200\nA strong opening\n\n2\n00:00:01,200 --> 00:00:02,400\nThe payoff\n"


def test_validate_srt_normalizes_numbering_and_rejects_bad_timing() -> None:
    normalized = validate_srt("9\n00:00:00.000 --> 00:00:01.000\nHello")
    assert normalized.startswith("1\n00:00:00,000 --> 00:00:01,000")
    with pytest.raises(ValueError, match="end time"):
        validate_srt("1\n00:00:02,000 --> 00:00:01,000\nBad order")
    with pytest.raises(ValueError, match="ascending"):
        validate_srt(
            "1\n00:00:03,000 --> 00:00:04,000\nLater\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nEarlier"
        )


def test_review_saves_immutable_revisions_and_restores_without_mutating_source(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "captions.srt").write_text(SRT, encoding="utf-8")
    (package / "title.txt").write_text("Original title\n", encoding="utf-8")
    (package / "final.v3.mp4").write_bytes(b"original-video-sentinel")

    seeded = load_review(package)
    first = save_review(package, {**seeded, "title": "Edited title", "captions_srt": SRT.replace("A strong opening", "A better opening")})
    second = save_review(package, {**first, "title": "Second title"})
    restored = restore_review(package, 1)

    assert first["version"] == 1
    assert second["version"] == 2
    assert restored["version"] == 3
    assert restored["title"] == "Edited title"
    assert restored["restored_from"] == 1
    assert (package / "final.v3.mp4").read_bytes() == b"original-video-sentinel"
    assert (package / "reviewed" / "captions.srt").read_text(encoding="utf-8").startswith("1\n")
    assert json.loads((package / "reviewed" / "review-overrides.json").read_text(encoding="utf-8"))["version"] == 3


def test_review_rejects_oversized_or_invalid_payloads(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    with pytest.raises(ValueError, match="Subtitle file exceeds"):
        validate_srt("x" * 500_001)
    with pytest.raises(ValueError, match="title must be text"):
        save_review(package, {"title": 123, "description": "", "captions_srt": ""})
