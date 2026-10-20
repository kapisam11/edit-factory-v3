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
    save_review(package, {**seed, "captions_srt": SRT.replace("A strong opening", "Corrected opening")})

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
    assert calls["kwargs"]["reuse_existing_subtitles"] is True
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


def test_load_review_converts_ass_and_prefers_upload_caption_srt_over_script_srt(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "script.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nScript fallback\n",
        encoding="utf-8",
    )
    (package / "upload" / "youtube_shorts").mkdir(parents=True)
    (package / "upload" / "youtube_shorts" / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nPrecisely timed caption\n",
        encoding="utf-8",
    )
    loaded = load_review(package)
    assert "Precisely timed caption" in loaded["captions_srt"]
    assert loaded["source_caption_file"] == "upload/youtube_shorts/captions.srt"

    ass_package = tmp_path / "ass-pkg"
    ass_package.mkdir()
    (ass_package / "captions.ass").write_text(
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:01.25,Default,,0,0,0,,First line\\Nsecond line, with comma\n",
        encoding="utf-8",
    )
    converted = load_review(ass_package)
    assert converted["captions_srt"] == (
        "1\n00:00:00,000 --> 00:00:01,250\nFirst line\nsecond line, with comma\n"
    )
    assert converted["source_caption_file"] == "captions.ass"


def test_compositor_does_not_overwrite_reviewed_srt(tmp_path: Path, monkeypatch) -> None:
    import ai_video_factory.composer as composer

    monkeypatch.setenv("AIVF_ENV", "test")
    monkeypatch.setenv("AIVF_TEST_DISABLE_EXTERNAL_TTS", "1")
    monkeypatch.setenv("AIVF_SMART_REFRAME", "0")
    package = tmp_path / "package"
    package.mkdir()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake-source")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-clip")
    reviewed_srt = (
        "1\n00:00:00,000 --> 00:00:00,600\nCorrected wording\n\n"
        "2\n00:00:00,600 --> 00:00:01,000\nCorrected payoff\n"
    )
    (package / "script.srt").write_text(reviewed_srt, encoding="utf-8")
    (package / "plan.json").write_text(
        json.dumps({
            "script": "Original plan script that must not replace reviewed captions.",
            "edit_plan": [[1.0, "hook"]],
            "v3_directives": {"blueprint_contract": "3.0.0", "platform_profile": {"width": 360, "height": 640}},
        }),
        encoding="utf-8",
    )
    captured = {}

    monkeypatch.setattr(composer, "_load_source_segments", lambda *_args: [(0.0, 1.0)])
    monkeypatch.setattr(composer, "detect_beats", lambda *_args: [])
    monkeypatch.setattr(composer, "generate_clip_paths", lambda *_args: [str(clip)])
    monkeypatch.setattr(composer, "_get_duration_safe", lambda *_args: 1.0)
    monkeypatch.setattr(composer, "snap_to_beat", lambda start, _end, duration, _beats: start + duration)
    monkeypatch.setattr(composer, "build_cinematic_filter", lambda *_args, **_kwargs: "null")
    monkeypatch.setattr(composer, "choose_encoder", lambda: "libx264")

    def fake_render_segment(_source, _start, _duration, _filter, destination):
        Path(destination).write_bytes(b"rendered-segment")

    def fake_concat(_list_path, output_path, encoder="libx264"):
        Path(output_path).write_bytes(b"concatenated-video")

    def fake_burn(_video, subtitle_path, output_path):
        captured["subtitle"] = Path(subtitle_path).read_text(encoding="utf-8")
        Path(output_path).write_bytes(b"final-video")

    monkeypatch.setattr(composer, "render_segment", fake_render_segment)
    monkeypatch.setattr(composer, "concat_segments", fake_concat)
    monkeypatch.setattr(composer, "burn_subtitles", fake_burn)
    monkeypatch.setattr(
        composer,
        "script_to_srt",
        lambda *_args, **_kwargs: pytest.fail("reviewed subtitle file must not be regenerated"),
    )

    output = package / "corrected.mp4"
    result = composer.compose_short_from_video(
        str(source),
        str(package),
        out_file=str(output),
        review=False,
        skip_qc=True,
        strict_voiceover=True,
        reuse_existing_voiceover=False,
        reuse_existing_subtitles=True,
    )

    assert result == str(output)
    assert captured["subtitle"] == reviewed_srt
    assert output.read_bytes() == b"final-video"


def test_metadata_only_review_edit_preserves_valid_corrected_render(tmp_path: Path) -> None:
    import hashlib

    package = tmp_path / "pkg"
    package.mkdir()
    first = save_review(package, {"title": "Original title", "description": "", "captions_srt": SRT})
    reviewed_video = package / "reviewed" / "renders" / "corrected.mp4"
    reviewed_video.parent.mkdir(parents=True)
    reviewed_video.write_bytes(b"validated-render")
    state_path = package / "reviewed" / "review-overrides.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({
        "render_status": "rendered",
        "rendered_for_version": first["version"],
        "rendered_at": "2026-10-20T10:00:00Z",
        "rendered_video_file": "reviewed/renders/corrected.mp4",
        "rendered_video_sha256": hashlib.sha256(reviewed_video.read_bytes()).hexdigest(),
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")

    second = save_review(package, {**state, "title": "New upload title"})

    assert second["version"] == 2
    assert second["render_status"] == "rendered"
    assert second["rendered_for_version"] == 2
    assert second["rendered_video_file"] == "reviewed/renders/corrected.mp4"
    assert second["rendered_video_sha256"] == hashlib.sha256(reviewed_video.read_bytes()).hexdigest()


def test_caption_edit_invalidates_corrected_render_link(tmp_path: Path) -> None:
    import hashlib

    package = tmp_path / "pkg"
    package.mkdir()
    first = save_review(package, {"title": "Original title", "description": "", "captions_srt": SRT})
    reviewed_video = package / "reviewed" / "renders" / "corrected.mp4"
    reviewed_video.parent.mkdir(parents=True)
    reviewed_video.write_bytes(b"validated-render")
    state_path = package / "reviewed" / "review-overrides.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({
        "rendered_video_file": "reviewed/renders/corrected.mp4",
        "rendered_video_sha256": hashlib.sha256(reviewed_video.read_bytes()).hexdigest(),
    })
    state_path.write_text(json.dumps(state), encoding="utf-8")

    next_review = save_review(
        package,
        {**state, "captions_srt": SRT.replace("A strong opening", "An improved opening")},
    )

    assert next_review["render_status"] == "requires_rerender"
    assert "rendered_video_file" not in next_review
    assert reviewed_video.is_file()
