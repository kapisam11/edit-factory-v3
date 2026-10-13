from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_video_factory import upload_package, v3_quality


def _ffprobe_payload(*, width: int = 1080, height: int = 1920, fps: str = "30/1", audio: bool = True) -> dict:
    streams = [{
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "width": width,
        "height": height,
        "avg_frame_rate": fps,
        "r_frame_rate": fps,
        "duration": "60.0",
    }]
    if audio:
        streams.append({
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "duration": "60.0",
        })
    return {
        "streams": streams,
        "format": {
            "duration": "60.0",
            "size": "1000",
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        },
    }


def test_upload_media_contract_validates_profile_dimensions_frame_rate_and_container(monkeypatch, tmp_path):
    media = tmp_path / "final.mp4"
    media.write_bytes(b"fake bytes; ffprobe is mocked")

    monkeypatch.setenv("AIVF_OUTPUT_FPS", "30")
    monkeypatch.setenv("AIVF_OUTPUT_FPS_TOLERANCE", "0.5")
    monkeypatch.setenv("AIVF_MAX_OUTPUT_BYTES", "2000")
    monkeypatch.setenv("AIVF_REQUIRE_OUTPUT_AUDIO", "1")
    monkeypatch.setattr(
        upload_package,
        "run_ffprobe",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps(_ffprobe_payload())
        ),
    )

    report = upload_package._media_contract_report(
        media, upload_package._probe_media(media), upload_package.get_output_profile("youtube_shorts")
    )
    assert report["passed"] is True
    assert report["checks"]["dimensions_match_profile"] is True
    assert report["checks"]["frame_rate_matches_policy"] is True
    assert report["checks"]["audio_present_when_required"] is True


def test_upload_media_contract_blocks_wrong_dimensions_and_frame_rate(monkeypatch, tmp_path):
    media = tmp_path / "final.mp4"
    media.write_bytes(b"fake bytes; ffprobe is mocked")
    monkeypatch.setenv("AIVF_OUTPUT_FPS", "30")
    monkeypatch.setenv("AIVF_OUTPUT_FPS_TOLERANCE", "0.5")
    monkeypatch.setenv("AIVF_MAX_OUTPUT_BYTES", "2000")
    monkeypatch.setattr(
        upload_package,
        "run_ffprobe",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps(_ffprobe_payload(width=640, height=360, fps="60/1"))
        ),
    )
    report = upload_package._media_contract_report(
        media, upload_package._probe_media(media), upload_package.get_output_profile("youtube_shorts")
    )
    assert report["passed"] is False
    assert report["checks"]["dimensions_match_profile"] is False
    assert report["checks"]["frame_rate_matches_policy"] is False


def test_upload_media_contract_blocks_missing_audio_when_required(monkeypatch, tmp_path):
    media = tmp_path / "final.mp4"
    media.write_bytes(b"fake bytes; ffprobe is mocked")
    monkeypatch.setenv("AIVF_REQUIRE_OUTPUT_AUDIO", "1")
    monkeypatch.setenv("AIVF_MAX_OUTPUT_BYTES", "2000")
    monkeypatch.setattr(
        upload_package,
        "run_ffprobe",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stderr="", stdout=json.dumps(_ffprobe_payload(audio=False))
        ),
    )
    report = upload_package._media_contract_report(
        media, upload_package._probe_media(media), upload_package.get_output_profile("youtube_shorts")
    )
    assert report["passed"] is False
    assert report["checks"]["audio_present_when_required"] is False


def test_upload_readiness_fails_closed_when_ffprobe_cannot_inspect_final_video(monkeypatch, tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    video = package / "final.mp4"
    video.write_bytes(b"invalid media")
    monkeypatch.setattr(
        upload_package,
        "run_ffprobe",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stderr="invalid media", stdout=""),
    )
    manifest = upload_package.finalize_upload_package(
        str(package),
        topic="Minecraft betrayal on SMP",
        summary={"facts_reviewed": True},
        script="A teammate made a surprising choice in a Minecraft SMP.",
        platform="youtube_shorts",
        final_video=str(video),
    )
    assert manifest["media_contract"]["passed"] is False
    assert manifest["publish_ready"] is False


def test_strict_render_check_enforces_fps_audio_and_output_size(monkeypatch):
    info = {
        "duration": 8.0,
        "size": 500,
        "width": 1080,
        "height": 1920,
        "fps": "60/1",
        "avg_frame_rate": "60/1",
        "r_frame_rate": "60/1",
        "has_audio": False,
        "format_name": "mp4",
    }
    monkeypatch.setattr(v3_quality, "probe_media", lambda _path: info)
    monkeypatch.setattr(v3_quality, "_sample_visual_changes", lambda *_args, **_kwargs: {"max_gap": 1.0})
    monkeypatch.setattr(v3_quality, "_verify_full_decode", lambda _path: None)
    monkeypatch.setenv("AIVF_MAX_OUTPUT_BYTES", "400")
    monkeypatch.setenv("AIVF_OUTPUT_FPS_TOLERANCE", "0.5")
    monkeypatch.setenv("AIVF_REQUIRE_OUTPUT_AUDIO", "1")

    report = v3_quality.strict_render_check(
        "final.mp4",
        target_seconds=8.0,
        platform_profile={"width": 1080, "height": 1920},
        retention_events=[{"time": 0.0}, {"time": 2.0}],
        expected_fps=30,
    )
    assert report["ok"] is False
    joined = " ".join(report["errors"])
    assert "frame rate" in joined
    assert "no audio stream" in joined
    assert "exceeds configured limit" in joined


def test_strict_render_check_does_not_require_audio_when_policy_says_optional(monkeypatch):
    info = {
        "duration": 8.0,
        "size": 500,
        "width": 1080,
        "height": 1920,
        "fps": "30/1",
        "avg_frame_rate": "30/1",
        "r_frame_rate": "30/1",
        "has_audio": False,
        "format_name": "mp4",
    }
    monkeypatch.setattr(v3_quality, "probe_media", lambda _path: info)
    monkeypatch.setattr(v3_quality, "_sample_visual_changes", lambda *_args, **_kwargs: {"max_gap": 1.0})
    monkeypatch.setattr(v3_quality, "_verify_full_decode", lambda _path: None)
    monkeypatch.setenv("AIVF_MAX_OUTPUT_BYTES", "2000")
    monkeypatch.setenv("AIVF_REQUIRE_OUTPUT_AUDIO", "0")

    report = v3_quality.strict_render_check(
        "silent-final.mp4",
        target_seconds=8.0,
        platform_profile={"width": 1080, "height": 1920},
        retention_events=[{"time": 0.0}, {"time": 2.0}],
    )
    assert report["ok"] is True
    assert "rendered artifact has no audio stream" in report["warnings"]
    assert any("human review" in warning.lower() for warning in report["warnings"])
    assert report["audio_required"] is False


def _approved_review_payload() -> dict:
    from datetime import datetime, timezone

    return {
        "reviewer": "local-reviewer",
        "approved": True,
        "scores": {
            "hook": 5,
            "pacing": 5,
            "visual_relevance": 5,
            "caption_readability": 5,
            "audio": 5,
            "rights": 5,
            "metadata": 5,
        },
        "notes": "Approved after local viewing.",
        "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _configure_publish_package_probe(monkeypatch, tmp_path: Path) -> Path:
    package_dir = tmp_path / "package"
    package_dir.mkdir(parents=True, exist_ok=True)
    video = package_dir / "final.mp4"
    video.write_bytes(b"x" * 1000)
    monkeypatch.setattr(upload_package, "_probe_media", lambda _path: _ffprobe_payload())
    monkeypatch.setenv("AIVF_ENV", "production")
    monkeypatch.setenv("AIVF_OUTPUT_FPS", "30")
    monkeypatch.setenv("AIVF_OUTPUT_FPS_TOLERANCE", "0.5")
    monkeypatch.setenv("AIVF_MAX_OUTPUT_BYTES", "2000")
    monkeypatch.setenv("AIVF_REQUIRE_OUTPUT_AUDIO", "1")
    monkeypatch.delenv("AIVF_REQUIRE_HUMAN_REVIEW", raising=False)
    return video


def test_upload_package_fails_closed_without_human_approval(monkeypatch, tmp_path: Path) -> None:
    video = _configure_publish_package_probe(monkeypatch, tmp_path)

    manifest = upload_package.finalize_upload_package(
        str(tmp_path / "package"),
        topic="Minecraft betrayal on SMP",
        summary={"facts_reviewed": True},
        final_video=str(video),
    )

    assert manifest["media_contract"]["passed"] is True
    assert manifest["media_rights"]["publish_blocked"] is False
    assert manifest["human_review"]["required"] is True
    assert manifest["human_review"]["publish_blocked"] is True
    assert manifest["publish_ready"] is False


def test_upload_package_fails_closed_when_required_rights_declaration_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    video = _configure_publish_package_probe(monkeypatch, tmp_path)

    manifest = upload_package.finalize_upload_package(
        str(tmp_path / "package"),
        topic="Minecraft betrayal on SMP",
        summary={
            "facts_reviewed": True,
            "human_review": _approved_review_payload(),
            "requires_rights_declaration": True,
        },
        final_video=str(video),
    )

    assert manifest["media_contract"]["passed"] is True
    assert manifest["human_review"]["status"] == "approved"
    assert manifest["media_rights"]["publish_blocked"] is True
    assert manifest["publish_ready"] is False


def test_upload_package_can_be_publish_ready_only_with_valid_review_and_rights(
    monkeypatch, tmp_path: Path
) -> None:
    from datetime import datetime, timezone

    video = _configure_publish_package_probe(monkeypatch, tmp_path)

    manifest = upload_package.finalize_upload_package(
        str(tmp_path / "package"),
        topic="Minecraft betrayal on SMP",
        summary={
            "facts_reviewed": True,
            "human_review": _approved_review_payload(),
            "source_credits": [
                {
                    "title": "Original gameplay footage",
                    "source": "user_provided",
                    "rights_basis": "owned",
                    "declared_by": "local-reviewer",
                    "declared_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            ],
        },
        final_video=str(video),
    )

    assert manifest["media_contract"]["passed"] is True
    assert manifest["metadata_quality"]["passed"] is True
    assert manifest["media_rights"]["publish_blocked"] is False
    assert manifest["human_review"]["status"] == "approved"
    assert manifest["publish_ready"] is True

