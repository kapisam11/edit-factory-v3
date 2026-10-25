from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from ai_video_factory.idea_to_video import (
    _eligible_visual,
    choose_surprise_topic,
    create_idea_video,
)
from app.job_service import build_job_params


def _settings() -> dict:
    return {
        "default_workflow": "v3",
        "default_target_seconds": 30,
        "default_v3_platform": "youtube_shorts",
        "default_v3_audience": "general short-form viewers",
        "default_v3_bpm": 120,
    }


def _commons_image(**overrides) -> dict:
    item = {
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/a1/example.jpg",
        "source": "wikimedia",
        "title": "Example image",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "evidence_url": "https://commons.wikimedia.org/wiki/File:Example.jpg",
        "artist": "Example Author",
        "rights_status": "cleared",
        "rights_basis": "cc_license",
    }
    item.update(overrides)
    return item


def test_idea_mode_accepts_blank_topic_and_chooses_surprise_topic():
    params = build_job_params(
        {"production_mode": "idea_to_video", "topic": "", "workflow": "v3"},
        _settings(),
        allow_skip_qc=False,
    )

    assert len(params["topic"]) >= 2
    assert params["production_mode"] == "idea_to_video"
    assert params["auto_generated_topic"] is True
    assert params["workflow"] == "default"
    assert params.get("raw_video") is None


def test_idea_mode_preserves_creator_topic():
    params = build_job_params(
        {
            "production_mode": "idea_to_video",
            "topic": "Why bees communicate through movement",
            "workflow": "v3",
        },
        _settings(),
        allow_skip_qc=False,
    )

    assert params["topic"] == "Why bees communicate through movement"
    assert params["auto_generated_topic"] is False
    assert params["workflow"] == "default"


def test_surprise_topic_is_nonempty_and_profile_aware():
    assert choose_surprise_topic().strip()
    assert choose_surprise_topic("gaming").strip()


@pytest.mark.parametrize(
    "item",
    [
        _commons_image(source="reddit", rights_status="cleared", rights_basis="cc_license"),
        _commons_image(source="youtube", rights_status="cleared", rights_basis="cc_license"),
        _commons_image(license="CC BY-NC 4.0"),
        _commons_image(license="CC BY-ND 4.0"),
        _commons_image(license="CC BY-SA 4.0"),
        _commons_image(url="https://example.com/image.jpg"),
        _commons_image(url="http://upload.wikimedia.org/wikipedia/commons/a/a1/example.jpg"),
        _commons_image(evidence_url="", license_url=""),
        _commons_image(rights_status="review_required"),
    ],
)
def test_idea_mode_rejects_unverified_or_restricted_assets(item):
    assert _eligible_visual(item) is False


def test_idea_mode_accepts_public_domain_commons_image():
    assert _eligible_visual(_commons_image(
        license="Public domain",
        rights_basis="public_domain",
    ))


def test_idea_mode_accepts_eligible_commons_video_only_with_video_metadata():
    item = _commons_image(
        url="https://upload.wikimedia.org/wikipedia/commons/a/a1/example.webm",
        source="wikimedia_video",
        mime="video/webm",
        license="CC BY 4.0",
        rights_basis="cc_license",
    )
    assert _eligible_visual(item)
    assert not _eligible_visual({**item, "mime": "image/webp"})


def test_idea_mode_fails_closed_when_only_unverified_sources_exist(tmp_path: Path, monkeypatch):
    import ai_video_factory.visuals_fetcher as visuals_fetcher

    monkeypatch.setattr(visuals_fetcher, "fetch_visuals", lambda *args, **kwargs: [
        {"source": "reddit", "url": "https://reddit.com/image.jpg", "rights_status": "review_required"},
        {"source": "youtube", "url": "https://www.youtube.com/watch?v=abc123"},
    ])
    ctx = SimpleNamespace(
        package_dir=str(tmp_path),
        topic="A test topic",
        script="Here is a short story about a surprising discovery.",
        target_seconds=15,
        voiceover=None,
        metrics={},
        warnings=[],
    )

    with pytest.raises(RuntimeError, match="could not find a downloadable Commons"):
        create_idea_video(ctx)


def test_idea_mode_renders_package_and_never_approves_publishing(tmp_path: Path, monkeypatch):
    import ai_video_factory.idea_to_video as idea_to_video
    import ai_video_factory.visuals_fetcher as visuals_fetcher

    asset_dir = tmp_path / "idea_assets"
    asset_dir.mkdir()
    source_image = asset_dir / "source.jpg"
    Image.new("RGB", (640, 480), (40, 100, 170)).save(source_image)

    item = _commons_image()
    monkeypatch.setattr(visuals_fetcher, "fetch_visuals", lambda *args, **kwargs: [item])
    monkeypatch.setattr(
        visuals_fetcher,
        "download_visuals",
        lambda visuals, out_dir, **kwargs: [{**visuals[0], "local_path": str(source_image)}],
    )
    monkeypatch.setattr(idea_to_video, "_duration", lambda path: None)
    monkeypatch.setattr(idea_to_video, "_download_licensed_music", lambda asset_dir: None)
    monkeypatch.setattr(
        idea_to_video,
        "run_ffmpeg",
        lambda command, **kwargs: (
            Path(command[-1]).parent.mkdir(parents=True, exist_ok=True),
            Path(command[-1]).write_bytes(b"synthetic-media"),
        ),
    )
    monkeypatch.setattr(
        idea_to_video,
        "validate_media_output",
        lambda *args, **kwargs: {
            "format": {"duration": "15.0"},
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        },
    )

    ctx = SimpleNamespace(
        package_dir=str(tmp_path),
        topic="A test topic",
        script="First the mystery appears. Then we discover the reason. Finally the result changes everything.",
        target_seconds=15,
        voiceover=None,
        metrics={},
        warnings=[],
        final_video=None,
    )
    result = create_idea_video(ctx)

    assert Path(result["final_video"]).is_file()
    assert ctx.final_video == result["final_video"]
    assert result["publish_ready"] is False
    rights = json.loads((tmp_path / "rights_manifest.json").read_text(encoding="utf-8"))
    assert rights["publication"]["approval_required"] is True
    assert rights["publication"]["publish_ready"] is False
    assert rights["publication"]["verified_live_destinations"] == []
    assert rights["assets"][0]["license_url"] == item["license_url"]
    assert rights["assets"][0]["evidence_url"] == item["evidence_url"]
    storyboard = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert storyboard["production_mode"] == "idea_to_video"
    assert storyboard["music_status"] == "no_eligible_Commons_music_found"
    assert storyboard["quality_validation"] == "passed"


def test_idea_mode_director_stage_packages_all_vertical_destinations_as_review_required(tmp_path: Path, monkeypatch):
    import ai_video_factory.complete_factory as complete_factory
    import ai_video_factory.idea_to_video as idea_module
    import ai_video_factory.upload_package as upload_package
    from ai_video_factory.pipeline import IdeaVideoStage, PipelineContext

    final_video = tmp_path / "final_idea_video.mp4"
    final_video.write_bytes(b"synthetic-final")
    captions = tmp_path / "captions.srt"
    captions.write_text("1\n00:00:00,000 --> 00:00:02,000\nA test caption.\n", encoding="utf-8")
    rights = {
        "schema_version": 1,
        "assets": [{
            "title": "Example image",
            "artist": "Example Author",
            "url": "https://upload.wikimedia.org/wikipedia/commons/a/a1/example.jpg",
            "license": "Public domain",
            "license_url": "https://commons.wikimedia.org/wiki/License",
            "evidence_url": "https://commons.wikimedia.org/wiki/File:Example.jpg",
            "rights_basis": "public_domain",
            "rights_status": "cleared",
            "declared_by": "wikimedia-commons-metadata",
            "declared_at": "2026-10-25T00:00:00Z",
        }],
        "audio_assets": [],
        "publication": {"status": "review_required", "publish_ready": False, "approval_required": True},
    }
    (tmp_path / "rights_manifest.json").write_text(json.dumps(rights), encoding="utf-8")

    def fake_create(ctx):
        ctx.final_video = str(final_video)
        return {
            "final_video": str(final_video),
            "asset_count": 1,
            "scene_count": 3,
            "voiceover_included": False,
            "music_included": False,
            "captions_srt": str(captions),
            "publish_ready": False,
        }

    def fake_render(source, package_dir, platforms):
        output = {}
        for platform in platforms:
            render = Path(package_dir) / "renders" / f"{platform}.mp4"
            render.parent.mkdir(parents=True, exist_ok=True)
            render.write_bytes(b"synthetic-platform-media")
            output[platform] = str(render)
        return output

    def fake_finalize(package_dir, **kwargs):
        return {
            "selected_title": "An example topic",
            "tags": ["example", "idea"],
            "publish_ready": True,
            "human_review": {"status": "approved", "approved": True},
        }

    monkeypatch.setattr(idea_module, "create_idea_video", fake_create)
    monkeypatch.setattr(complete_factory, "render_all_platforms", fake_render)
    monkeypatch.setattr(upload_package, "finalize_upload_package", fake_finalize)

    ctx = PipelineContext(
        topic="An example topic",
        package_dir=str(tmp_path),
        production_mode="idea_to_video",
    )
    ctx.final_video = str(final_video)
    ctx.script = "An example topic turns into a short video."
    ctx.thumbnail = None
    ctx.plan = {"title": "An example topic"}
    ctx.metadata = {}
    ctx.research = {}

    IdeaVideoStage().run(ctx)

    expected = {"youtube_shorts", "tiktok", "instagram_reels"}
    assert set(ctx.research["idea_to_video"]["upload_packages"]) == expected
    assert ctx.research["idea_to_video"]["publish_ready"] is False
    assert ctx.metadata["publication_status"] == "review_required"
    assert ctx.metadata["publish_ready"] is False
    for platform in expected:
        manifest = json.loads(
            (tmp_path / "upload" / platform / "metadata.json").read_text(encoding="utf-8")
        )
        assert manifest["publish_ready"] is False
        assert manifest["human_review"]["status"] == "review_required"
        assert manifest["publication_policy"]["approval_required"] is True
    upload_index = json.loads((tmp_path / "upload_package.json").read_text(encoding="utf-8"))
    assert set(upload_index["platforms"]) == expected
    assert upload_index["publication_policy"]["publish_ready"] is False


def test_director_auto_edit_stage_does_not_fail_before_idea_renderer(tmp_path: Path):
    from ai_video_factory.pipeline import AutoEditStage, PipelineContext

    ctx = PipelineContext(
        topic="A topic without source media",
        package_dir=str(tmp_path),
        production_mode="idea_to_video",
    )

    result = AutoEditStage().run(ctx)

    assert result is ctx
    assert ctx.final_video is None
    assert any("storyboard next" in warning for warning in ctx.warnings)


def test_pipeline_context_rejects_unknown_production_modes():
    from ai_video_factory.pipeline import PipelineContext

    with pytest.raises(ValueError, match="production_mode must be"):
        PipelineContext(topic="A test topic", production_mode="magic")
