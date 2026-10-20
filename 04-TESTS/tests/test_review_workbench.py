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

 
def test_review_and_revision_integrity_checks_reject_tampered_sidecars(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    first = save_review(package, {"title": "Version one", "description": "", "captions_srt": SRT})
    save_review(package, {**first, "title": "Version two"})

    revision = package / "reviewed" / "revisions" / "rev-000001.json"
    revision_data = json.loads(revision.read_text(encoding="utf-8"))
    revision_data["title"] = "Tampered title"
    revision.write_text(json.dumps(revision_data), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity check"):
        restore_review(package, 1)

    state_path = package / "reviewed" / "review-overrides.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["title"] = "Tampered latest title"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity check"):
        load_review(package)


def test_render_reviewed_video_uses_isolated_workspace_and_preserves_original(tmp_path: Path, monkeypatch) -> None:
    import hashlib
    import ai_video_factory.composer as composer
    import ai_video_factory.render_engine as render_engine
    from ai_video_factory.review_workbench import render_reviewed_video

    package = tmp_path / "pkg"
    package.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-video-sentinel")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (package / "source_manifest.json").write_text(
        json.dumps({"source_path": str(source), "source_sha256": digest}),
        encoding="utf-8",
    )
    (package / "plan.json").write_text(
        json.dumps({"v3_directives": {"blueprint_contract": "3.0.0"}, "edit_plan": [[2.0, "hook"]], "script": "hello"}),
        encoding="utf-8",
    )
    (package / "timeline.json").write_text("{}", encoding="utf-8")
    (package / "voice.mp3").write_bytes(b"original-voice-sentinel")
    original_video = package / "final.v3.mp4"
    original_video.write_bytes(b"original-final-video-sentinel")
    (package / "captions.srt").write_text(SRT, encoding="utf-8")
    seed = load_review(package)
    save_review(package, {**seed, "captions_srt": SRT.replace("Opening line", "Corrected opening")})

    calls = {}

    def fake_compose(source_path, package_dir, **kwargs):
        work = Path(package_dir)
        calls["source"] = source_path
        calls["workspace"] = work
        calls["kwargs"] = kwargs
        assert not (work / "captions.ass").exists()
        assert "Corrected opening" in (work / "script.srt").read_text(encoding="utf-8")
        assert (work / "voice.mp3").read_bytes() == b"original-voice-sentinel"
        Path(kwargs["out_file"]).write_bytes(b"rendered-reviewed-video")
        return kwargs["out_file"]

    monkeypatch.setattr(composer, "compose_short_from_video", fake_compose)
    monkeypatch.setattr(render_engine, "validate_media_output", lambda *_a, **_kw: {"format": {"duration": "2.0"}})

    result = render_reviewed_video(package)

    assert calls["source"] == str(source.resolve())
    assert calls["kwargs"]["strict_voiceover"] is True
    assert calls["kwargs"]["reuse_existing_voiceover"] is True
    assert calls["kwargs"]["review"] is False
    assert original_video.read_bytes() == b"original-final-video-sentinel"
    assert (package / "plan.json").read_text(encoding="utf-8").find("blueprint_contract") >= 0
    rendered = package / result["rendered_video_file"]
    assert rendered.read_bytes() == b"rendered-reviewed-video"
    assert result["render_status"] == "rendered"
    assert result["rendered_for_version"] == result["version"]
    assert load_review(package)["rendered_video_file"] == result["rendered_video_file"]


def test_render_reviewed_video_refuses_changed_source(tmp_path: Path) -> None:
    import hashlib
    from ai_video_factory.review_workbench import render_reviewed_video

    package = tmp_path / "pkg"
    package.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original")
    (package / "source_manifest.json").write_text(
        json.dumps({"source_path": str(source), "source_sha256": hashlib.sha256(b"original").hexdigest()}),
        encoding="utf-8",
    )
    source.write_bytes(b"changed")
    (package / "plan.json").write_text(
        json.dumps({"v3_directives": {"blueprint_contract": "3.0.0"}, "edit_plan": [[2.0, "hook"]]}),
        encoding="utf-8",
    )
    (package / "timeline.json").write_text("{}", encoding="utf-8")
    (package / "voice.mp3").write_bytes(b"voice")
    (package / "captions.srt").write_text(SRT, encoding="utf-8")
    seed = load_review(package)
    save_review(package, {**seed, "captions_srt": SRT.replace("Opening line", "Corrected opening")})

    with pytest.raises(ValueError, match="source has changed"):
        render_reviewed_video(package)
    assert not (package / "reviewed" / "renders").exists()
