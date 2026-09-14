from pathlib import Path

from ai_video_factory.content_factory import CheckpointStore, dependency_fingerprint
from ai_video_factory.upload_package import OUTPUT_PROFILES, rank_title_candidates


def test_title_laboratory_returns_twenty_candidates():
    titles = rank_title_candidates("Minecraft betrayal on SMP", max_candidates=20)
    assert 1 <= len(titles) <= 20
    assert len({item["title"] for item in titles}) == len(titles)
    assert all("score" in item and "reasons" in item for item in titles)


def test_upload_profiles_include_all_requested_formats():
    assert OUTPUT_PROFILES["youtube_shorts"].aspect_ratio == "9:16"
    assert OUTPUT_PROFILES["youtube"].aspect_ratio == "16:9"
    assert OUTPUT_PROFILES["square"].aspect_ratio == "1:1"
    assert "tiktok" in OUTPUT_PROFILES
    assert "instagram_reels" in OUTPUT_PROFILES


def test_checkpoint_store_is_resumable(tmp_path: Path):
    store = CheckpointStore(str(tmp_path / "checkpoints.json"))
    assert not store.is_complete("render")
    store.mark("render", status="complete", artifacts=["final.mp4"])
    assert store.is_complete("render")


def test_dependency_fingerprint_is_deterministic():
    assert dependency_fingerprint({"b": 2, "a": 1}) == dependency_fingerprint({"a": 1, "b": 2})
