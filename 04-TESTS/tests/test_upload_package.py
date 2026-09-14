from pathlib import Path

from ai_video_factory.upload_package import (
    finalize_upload_package,
    generate_platform_tags,
    rank_title_candidates,
)


def test_title_ranking_prefers_topic_specific_curiosity():
    ranked = rank_title_candidates(
        "Minecraft betrayal on SMP",
        hook="Nobody saw this coming",
        strongest_angle="A trusted teammate stole everything",
        max_candidates=5,
    )
    assert ranked
    assert ranked[0]["title"]
    assert ranked[0]["score"] >= ranked[-1]["score"]


def test_tags_are_relevant_and_exclude_generic_spam():
    tags = generate_platform_tags(
        "Minecraft betrayal on SMP",
        "A story about a Minecraft SMP betrayal and the aftermath.",
        "Minecraft SMP betrayal",
        max_tags=20,
    )
    assert tags
    assert "viral" not in tags
    assert "youtube" not in tags
    assert any("minecraft" in tag for tag in tags)


def test_finalize_upload_package_writes_copy_paste_bundle(tmp_path: Path):
    package_dir = tmp_path / "job"
    package_dir.mkdir()
    video = package_dir / "final.mp4"
    video.write_bytes(b"fake-media")

    manifest = finalize_upload_package(
        str(package_dir),
        topic="Minecraft betrayal on SMP",
        summary={
            "hook": "Nobody saw this coming",
            "strongest_angle": "A trusted teammate stole everything",
            "emotion": "dramatic",
        },
        script="Nobody saw this coming. The teammate stole everything.",
        platform="youtube_shorts",
        final_video=str(video),
    )

    upload_dir = package_dir / "upload"
    assert (upload_dir / "title.txt").is_file()
    assert (upload_dir / "description.txt").is_file()
    assert (upload_dir / "tags.txt").is_file()
    assert (upload_dir / "metadata.json").is_file()
    assert (upload_dir / "README-UPLOAD.md").is_file()
    assert manifest["selected_title"]
    assert manifest["files"]["video"] == "final.mp4"
    assert manifest["artifacts"]["final.mp4"]["sha256"]
